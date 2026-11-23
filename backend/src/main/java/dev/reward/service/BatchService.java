package dev.reward.service;

import dev.reward.api.ApiException;
import dev.reward.api.ApiModels.BatchDistributionResponse;
import dev.reward.api.ApiModels.BatchRunResponse;
import dev.reward.api.ApiModels.ClaimResponse;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Service;
import org.springframework.transaction.PlatformTransactionManager;
import org.springframework.transaction.TransactionDefinition;
import org.springframework.transaction.support.TransactionTemplate;

import javax.sql.DataSource;
import java.nio.charset.StandardCharsets;
import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import java.util.function.Supplier;

/** 예약 대상의 업무 재개 기준은 batch_runs가 아니라 DB의 실제 grant 행이다. */
@Service
public class BatchService {
    private static final int DEFAULT_PAGE_SIZE = 1000;
    private static final int MAX_PAGE_SIZE = 1000;

    private final JdbcTemplate jdbc;
    private final DataSource dataSource;
    private final RewardGrantProcedure grantProcedure;
    private final TransactionTemplate transaction;

    public BatchService(
            JdbcTemplate jdbc,
            DataSource dataSource,
            RewardGrantProcedure grantProcedure,
            PlatformTransactionManager transactionManager
    ) {
        this.jdbc = jdbc;
        this.dataSource = dataSource;
        this.grantProcedure = grantProcedure;
        this.transaction = new TransactionTemplate(transactionManager);
        // 한 사용자별 별도 커밋을 강제해 상위 호출자가 있어도 전체 배치로 묶이지 않게 한다.
        this.transaction.setPropagationBehavior(TransactionDefinition.PROPAGATION_REQUIRES_NEW);
        // 지급 procedure는 advisory/unique lock 대기 뒤 새 문장의 snapshot으로 승자 행을 읽는다.
        this.transaction.setIsolationLevel(TransactionDefinition.ISOLATION_READ_COMMITTED);
    }

    /** 수동/스케줄러 진입점. DB 세션 락을 잡은 뒤 사용자 단위로 독립 커밋한다. */
    public BatchRunResponse run(long eventId, Integer requestedLimit) {
        int limit = requestedLimit == null ? DEFAULT_PAGE_SIZE : requestedLimit;
        if (limit < 1 || limit > MAX_PAGE_SIZE) {
            throw ApiException.badRequest("한 번의 BATCH 실행 범위는 1명부터 1000명까지입니다.");
        }

        try (Connection lockConnection = dataSource.getConnection()) {
            if (!tryAcquireEventLock(lockConnection, eventId)) {
                throw ApiException.conflict("같은 이벤트의 BATCH 작업이 이미 실행 중입니다.");
            }
            try {
                return runLocked(eventId, limit);
            } finally {
                releaseEventLock(lockConnection, eventId);
            }
        } catch (SQLException exception) {
            throw new IllegalStateException("BATCH 작업 잠금을 관리하지 못했습니다.", exception);
        }
    }

    public BatchDistributionResponse distribution(long eventId) {
        BatchEvent event = findBatchEvent(eventId);
        // 대상 편집은 DRAFT에서만 필요하므로 열린 이벤트의 진행률 응답을 작게 유지한다.
        List<Long> recipientIds = "DRAFT".equals(event.status())
                ? jdbc.queryForList("""
                    SELECT user_id
                    FROM reward.event_recipients
                    WHERE event_id = ?
                    ORDER BY user_id
                    """, Long.class, eventId)
                : List.of();

        GrantCounts counts = jdbc.queryForObject("""
                SELECT COUNT(*)::INTEGER AS recipient_count,
                       COUNT(g.id)::INTEGER AS granted_count
                FROM reward.event_recipients r
                LEFT JOIN reward.reward_grants g
                  ON g.event_id = r.event_id AND g.user_id = r.user_id
                WHERE r.event_id = ?
                """, (resultSet, rowNum) -> new GrantCounts(
                resultSet.getInt("recipient_count"), resultSet.getInt("granted_count")), eventId);

        List<BatchRunResponse> runs = jdbc.query("""
                SELECT id, event_id, status, target_count, paid_count, replayed_count,
                       already_granted_count, not_available_count, failed_count,
                       started_at, finished_at, error_summary
                FROM reward.batch_runs
                WHERE event_id = ?
                ORDER BY started_at DESC, id DESC
                LIMIT 5
                """, BatchService::mapRun, eventId);

        return new BatchDistributionResponse(
                eventId, event.status(), event.scheduledAt(), counts.recipientCount(),
                counts.grantedCount(), counts.recipientCount() - counts.grantedCount(), recipientIds, runs);
    }

    private BatchRunResponse runLocked(long eventId, int limit) {
        RunStart start = inNewTransaction(() -> {
            BatchEvent event = findBatchEvent(eventId);
            if (!"BATCH".equals(event.mode())) {
                throw ApiException.conflict("BATCH 이벤트만 일괄 지급을 실행할 수 있습니다.");
            }
            if (!"OPEN".equals(event.status())) {
                throw ApiException.conflict("OPEN 상태의 BATCH 이벤트만 실행할 수 있습니다.");
            }
            if (!event.scheduleReached()) {
                throw ApiException.conflict("예약 시각이 지나야 BATCH 지급을 실행할 수 있습니다.");
            }

            // 이전 프로세스가 비정상 종료했다면 세션 락은 해제되고 RUNNING 행만 남는다.
            jdbc.update("""
                    UPDATE reward.batch_runs
                    SET status = 'FAILED', finished_at = clock_timestamp(),
                        error_summary = COALESCE(error_summary, '워커 중단 뒤 재실행에서 종료 처리됨')
                    WHERE event_id = ? AND status = 'RUNNING'
                    """, eventId);

            List<Long> pendingIds = jdbc.query("""
                    SELECT r.user_id
                    FROM reward.event_recipients r
                    LEFT JOIN reward.reward_grants g
                      ON g.event_id = r.event_id AND g.user_id = r.user_id
                    WHERE r.event_id = ? AND g.id IS NULL
                    ORDER BY r.user_id
                    LIMIT ?
                    """, (resultSet, rowNum) -> resultSet.getLong(1), eventId, limit);

            Long runId = jdbc.queryForObject("""
                    INSERT INTO reward.batch_runs (event_id, status, target_count)
                    VALUES (?, 'RUNNING', ?)
                    RETURNING id
                    """, Long.class, eventId, pendingIds.size());
            return new RunStart(runId, pendingIds);
        });

        for (long userId : start.pendingUserIds()) {
            UUID requestId = stableRequestId(eventId, userId);
            try {
                inNewTransaction(() -> {
                    ClaimResponse result = grantProcedure.call(eventId, userId, requestId, "BATCH");
                    recordResult(start.runId(), result.result());
                    return null;
                });
            } catch (RuntimeException exception) {
                // 한 대상의 오류를 기록하고 다음 대상은 계속 처리한다. DB 단절이면 기록도 실패해 RUNNING으로 남는다.
                inNewTransaction(() -> {
                    recordFailure(start.runId(), exception);
                    return null;
                });
            }
        }

        return inNewTransaction(() -> finishRun(start.runId()));
    }

    private void recordResult(long runId, String result) {
        String column = switch (result) {
            case "PAID" -> "paid_count";
            case "REPLAYED" -> "replayed_count";
            case "ALREADY_GRANTED" -> "already_granted_count";
            case "NOT_AVAILABLE" -> "not_available_count";
            default -> throw new IllegalStateException("Unexpected grant procedure result: " + result);
        };
        // 컬럼명은 위의 고정 allowlist에서만 오므로 값 바인딩 대신 식별자로 넣어도 안전하다.
        int updated = jdbc.update("UPDATE reward.batch_runs SET " + column + " = " + column
                + " + 1 WHERE id = ? AND status = 'RUNNING'", runId);
        if (updated != 1) {
            throw new IllegalStateException("BATCH 실행 기록이 RUNNING 상태가 아닙니다.");
        }
    }

    private void recordFailure(long runId, RuntimeException exception) {
        String detail = exception.getMessage() == null ? exception.getClass().getSimpleName() : exception.getMessage();
        if (detail.length() > 500) detail = detail.substring(0, 500);
        jdbc.update("""
                UPDATE reward.batch_runs
                SET failed_count = failed_count + 1,
                    error_summary = LEFT(COALESCE(error_summary || chr(10), '') || ?, 2000)
                WHERE id = ? AND status = 'RUNNING'
                """, detail, runId);
    }

    private BatchRunResponse finishRun(long runId) {
        jdbc.update("""
                UPDATE reward.batch_runs
                SET status = CASE WHEN failed_count = 0 AND not_available_count = 0
                                  THEN 'COMPLETED' ELSE 'FAILED' END,
                    finished_at = clock_timestamp(),
                    error_summary = CASE WHEN not_available_count > 0
                                         THEN COALESCE(error_summary || chr(10), '')
                                              || '일부 대상은 이벤트 상태/예약 정책상 지급되지 않았습니다.'
                                         ELSE error_summary END
                WHERE id = ? AND status = 'RUNNING'
                """, runId);
        return jdbc.queryForObject("""
                SELECT id, event_id, status, target_count, paid_count, replayed_count,
                       already_granted_count, not_available_count, failed_count,
                       started_at, finished_at, error_summary
                FROM reward.batch_runs
                WHERE id = ?
                """, BatchService::mapRun, runId);
    }

    private BatchEvent findBatchEvent(long eventId) {
        List<BatchEvent> events = jdbc.query("""
                SELECT mode, status, scheduled_at,
                       scheduled_at <= clock_timestamp() AS schedule_reached
                FROM reward.events
                WHERE id = ?
                """, (resultSet, rowNum) -> new BatchEvent(
                resultSet.getString("mode"), resultSet.getString("status"),
                toInstant(resultSet.getTimestamp("scheduled_at")), resultSet.getBoolean("schedule_reached")), eventId);
        return events.stream().findFirst().orElseThrow(() -> ApiException.notFound("이벤트를 찾을 수 없습니다."));
    }

    private boolean tryAcquireEventLock(Connection connection, long eventId) throws SQLException {
        try (PreparedStatement statement = connection.prepareStatement(
                "SELECT pg_try_advisory_lock(hashtextextended(?, 0))")) {
            statement.setString(1, "reward.batch:" + eventId);
            try (ResultSet resultSet = statement.executeQuery()) {
                resultSet.next();
                return resultSet.getBoolean(1);
            }
        }
    }

    private void releaseEventLock(Connection connection, long eventId) {
        try (PreparedStatement statement = connection.prepareStatement(
                "SELECT pg_advisory_unlock(hashtextextended(?, 0))")) {
            statement.setString(1, "reward.batch:" + eventId);
            statement.execute();
        } catch (SQLException ignored) {
            // PostgreSQL disconnect/restart releases session locks; closing this connection is the fallback.
        }
    }

    private static UUID stableRequestId(long eventId, long userId) {
        // UUID v3 deterministically identifies one user/event even after worker or API restart.
        String key = "reward.batch:" + eventId + ":" + userId;
        return UUID.nameUUIDFromBytes(key.getBytes(StandardCharsets.UTF_8));
    }

    private static BatchRunResponse mapRun(ResultSet row, int rowNum) throws SQLException {
        return new BatchRunResponse(
                row.getLong("id"), row.getLong("event_id"), row.getString("status"),
                row.getInt("target_count"), row.getInt("paid_count"), row.getInt("replayed_count"),
                row.getInt("already_granted_count"), row.getInt("not_available_count"), row.getInt("failed_count"),
                toInstant(row.getTimestamp("started_at")), toInstant(row.getTimestamp("finished_at")),
                row.getString("error_summary"));
    }

    private static Instant toInstant(Timestamp value) {
        return value == null ? null : value.toInstant();
    }

    private <T> T inNewTransaction(Supplier<T> operation) {
        return transaction.execute(status -> operation.get());
    }

    private record BatchEvent(String mode, String status, Instant scheduledAt, boolean scheduleReached) {}
    private record GrantCounts(int recipientCount, int grantedCount) {}
    private record RunStart(long runId, List<Long> pendingUserIds) {}
}
