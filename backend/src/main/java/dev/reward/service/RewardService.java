package dev.reward.service;

import dev.reward.api.ApiException;
import dev.reward.api.ApiModels.ClaimResponse;
import dev.reward.api.ApiModels.CompensationRequest;
import dev.reward.api.ApiModels.CompensationResponse;
import dev.reward.api.ApiModels.CreateEventRequest;
import dev.reward.api.ApiModels.EventResponse;
import dev.reward.api.ApiModels.LedgerEntryResponse;
import dev.reward.api.ApiModels.PayoutEntryResponse;
import dev.reward.api.ApiModels.RecipientUpdateResponse;
import dev.reward.api.ApiModels.WalletResponse;
import dev.reward.api.ApiModels.WalletResetResponse;
import org.springframework.dao.EmptyResultDataAccessException;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.jdbc.core.RowMapper;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.sql.ResultSet;
import java.sql.SQLException;
import java.sql.Timestamp;
import java.time.Instant;
import java.util.ArrayList;
import java.util.List;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;

@Service
public class RewardService {
    private record WalletResetResult(
            String result,
            long usersReset,
            long grantsDeleted,
            long compensationsDeleted,
            long ledgerRowsDeleted
    ) {}

    private static final String EVENT_SELECT = """
            SELECT e.id, e.name, e.mode, e.status, e.starts_at, e.ends_at, e.scheduled_at,
                   e.reward_money, e.reward_point,
                   (SELECT count(*) FROM reward.event_recipients r WHERE r.event_id = e.id) AS recipient_count,
                   EXISTS (
                       SELECT 1 FROM reward.reward_grants g
                       WHERE g.event_id = e.id AND g.user_id = ?
                   ) AS claimed
            FROM reward.events e
            """;

    private static final RowMapper<EventResponse> EVENT_ROW_MAPPER = RewardService::mapEvent;
    private final JdbcTemplate jdbc;
    private final RewardGrantProcedure grantProcedure;
    private final RewardCompensationProcedure compensationProcedure;

    public RewardService(
            JdbcTemplate jdbc,
            RewardGrantProcedure grantProcedure,
            RewardCompensationProcedure compensationProcedure
    ) {
        this.jdbc = jdbc;
        this.grantProcedure = grantProcedure;
        this.compensationProcedure = compensationProcedure;
    }

    @Transactional(readOnly = true)
    public List<EventResponse> listEvents(long userId, String status) {
        ensureUser(userId);
        String normalizedStatus = normalizeStatusFilter(status);
        if (normalizedStatus == null) {
            return jdbc.query(EVENT_SELECT + """
                    ORDER BY CASE e.status WHEN 'OPEN' THEN 0 WHEN 'DRAFT' THEN 1 ELSE 2 END,
                             e.starts_at DESC, e.id DESC
                    """, EVENT_ROW_MAPPER, userId);
        }
        return jdbc.query(EVENT_SELECT + """
                WHERE e.status = ?
                ORDER BY e.starts_at DESC, e.id DESC
                """, EVENT_ROW_MAPPER, userId, normalizedStatus);
    }

    @Transactional(readOnly = true)
    public EventResponse getEvent(long userId, long eventId) {
        ensureUser(userId);
        return findEvent(userId, eventId).orElseThrow(() -> ApiException.notFound("이벤트를 찾을 수 없습니다."));
    }

    @Transactional
    public EventResponse createEvent(long actorUserId, CreateEventRequest request) {
        ensureUser(actorUserId);
        String mode = request.mode().trim().toUpperCase(Locale.ROOT);
        validateCreateEvent(request, mode);

        Timestamp scheduledAt = "BATCH".equals(mode) ? Timestamp.from(request.scheduledAt()) : null;
        Long eventId = jdbc.queryForObject("""
                INSERT INTO reward.events (
                    name, mode, status, starts_at, ends_at, scheduled_at, reward_money, reward_point
                ) VALUES (?, ?, 'DRAFT', ?, ?, ?, ?, ?)
                RETURNING id
                """, Long.class,
                request.name().trim(), mode,
                Timestamp.from(request.startsAt()), Timestamp.from(request.endsAt()), scheduledAt,
                request.rewardMoney(), request.rewardPoint());

        jdbc.update("""
                INSERT INTO reward.event_status_audit (
                    event_id, previous_status, new_status, changed_by_user_id
                ) VALUES (?, NULL, 'DRAFT', ?)
                """, eventId, actorUserId);
        return findEvent(actorUserId, eventId).orElseThrow();
    }

    @Transactional
    public EventResponse changeEventStatus(long actorUserId, long eventId, String nextStatus) {
        ensureUser(actorUserId);
        EventResponse current = findEventForUpdate(actorUserId, eventId)
                .orElseThrow(() -> ApiException.notFound("이벤트를 찾을 수 없습니다."));

        boolean allowed = ("OPEN".equals(nextStatus) && "DRAFT".equals(current.status()))
                || ("CLOSED".equals(nextStatus) && "OPEN".equals(current.status()));
        if (!allowed) {
            throw ApiException.conflict("이벤트 상태는 DRAFT → OPEN → CLOSED 순서로만 바꿀 수 있습니다.");
        }
        if ("OPEN".equals(nextStatus) && "BATCH".equals(current.mode()) && current.recipientCount() == 0) {
            throw ApiException.conflict("BATCH 이벤트는 대상자 등록 후 오픈할 수 있습니다. 대상자 등록은 P2 범위입니다.");
        }

        jdbc.update("UPDATE reward.events SET status = ? WHERE id = ?", nextStatus, eventId);
        jdbc.update("""
                INSERT INTO reward.event_status_audit (
                    event_id, previous_status, new_status, changed_by_user_id
                ) VALUES (?, ?, ?, ?)
                """, eventId, current.status(), nextStatus, actorUserId);
        return findEvent(actorUserId, eventId).orElseThrow();
    }

    @Transactional
    public RecipientUpdateResponse replaceBatchRecipients(long actorUserId, long eventId, List<Long> userIds) {
        ensureUser(actorUserId);
        EventResponse event = findEventForUpdate(actorUserId, eventId)
                .orElseThrow(() -> ApiException.notFound("이벤트를 찾을 수 없습니다."));
        if (!"BATCH".equals(event.mode()) || !"DRAFT".equals(event.status())) {
            throw ApiException.conflict("BATCH 대상 명단은 DRAFT 상태에서만 확정할 수 있습니다.");
        }

        Set<Long> uniqueIds = new HashSet<>(userIds);
        if (uniqueIds.size() != userIds.size()) {
            throw ApiException.badRequest("대상 명단에 중복된 사용자 ID가 있습니다.");
        }

        List<Long> foundUsers = List.of();
        if (!userIds.isEmpty()) {
            String placeholders = String.join(",", java.util.Collections.nCopies(userIds.size(), "?"));
            foundUsers = jdbc.queryForList(
                    "SELECT id FROM reward.users WHERE id IN (" + placeholders + ") ORDER BY id",
                    Long.class, userIds.toArray());
        }
        if (foundUsers.size() != userIds.size()) {
            Set<Long> missingIds = new HashSet<>(userIds);
            missingIds.removeAll(foundUsers);
            throw ApiException.badRequest("등록되지 않은 사용자 ID가 있습니다: " + missingIds);
        }

        // DRAFT에서만 기존 명단을 통째로 교체해 운영자가 최종 대상을 확인하게 한다.
        jdbc.update("DELETE FROM reward.event_recipients WHERE event_id = ?", eventId);
        if (!userIds.isEmpty()) {
            jdbc.batchUpdate("""
                    INSERT INTO reward.event_recipients (event_id, user_id)
                    VALUES (?, ?)
                    """, userIds, userIds.size(), (statement, userId) -> {
                statement.setLong(1, eventId);
                statement.setLong(2, userId);
            });
        }
        return new RecipientUpdateResponse(eventId, userIds.size());
    }

    @Transactional
    public ClaimResponse claim(long userId, long eventId, UUID requestId) {
        ensureUser(userId);
        return grantProcedure.call(eventId, userId, requestId, "CLAIM");
    }

    @Transactional
    public CompensationResponse compensate(long operatorUserId, CompensationRequest request) {
        ensureUser(operatorUserId);
        ensureUser(request.userId());
        if (request.moneyAmount() == 0 && request.pointAmount() == 0) {
            throw ApiException.badRequest("Money 또는 Point 보상 중 하나는 0보다 커야 합니다.");
        }
        Boolean eventExists = jdbc.queryForObject(
                "SELECT EXISTS (SELECT 1 FROM reward.events WHERE id = ?)", Boolean.class, request.eventId());
        if (!Boolean.TRUE.equals(eventExists)) {
            throw ApiException.notFound("관련 이벤트를 찾을 수 없습니다.");
        }
        Boolean walletExists = jdbc.queryForObject(
                "SELECT EXISTS (SELECT 1 FROM reward.wallets WHERE user_id = ?)", Boolean.class, request.userId());
        if (!Boolean.TRUE.equals(walletExists)) {
            throw ApiException.notFound("대상 사용자의 지갑을 찾을 수 없습니다.");
        }
        return compensationProcedure.call(operatorUserId, request);
    }

    @Transactional
    public WalletResetResponse resetLocalUserWallet(long userId) {
        WalletResetResult reset = jdbc.queryForObject("""
                SELECT result, users_reset, grants_deleted, compensations_deleted, ledger_rows_deleted
                FROM reward.reset_local_user_wallet(?)
                """, (resultSet, rowNum) -> new WalletResetResult(
                resultSet.getString("result"),
                resultSet.getLong("users_reset"),
                resultSet.getLong("grants_deleted"),
                resultSet.getLong("compensations_deleted"),
                resultSet.getLong("ledger_rows_deleted")), userId);

        return toWalletResetResponse(reset, "선택한 사용자의 지갑과 지급 이력을 초기화했습니다.");
    }

    @Transactional
    public WalletResetResponse resetAllLocalWallets() {
        WalletResetResult reset = jdbc.queryForObject("""
                SELECT result, users_reset, grants_deleted, compensations_deleted, ledger_rows_deleted
                FROM reward.reset_all_local_wallets()
                """, (resultSet, rowNum) -> new WalletResetResult(
                resultSet.getString("result"),
                resultSet.getLong("users_reset"),
                resultSet.getLong("grants_deleted"),
                resultSet.getLong("compensations_deleted"),
                resultSet.getLong("ledger_rows_deleted")));

        return toWalletResetResponse(reset, "전체 사용자의 지갑과 지급 이력을 초기화했습니다.");
    }

    private static WalletResetResponse toWalletResetResponse(WalletResetResult reset, String successMessage) {
        return switch (reset.result()) {
            case "RESET" -> new WalletResetResponse(
                    reset.usersReset(), reset.grantsDeleted(), reset.compensationsDeleted(),
                    reset.ledgerRowsDeleted(), successMessage);
            case "USER_NOT_FOUND" -> throw ApiException.notFound("데모 사용자를 찾을 수 없습니다.");
            case "WALLET_NOT_FOUND" -> throw ApiException.notFound("사용자 지갑을 찾을 수 없습니다.");
            case "BATCH_RUNNING" -> throw ApiException.conflict(
                    "BATCH 작업이 실행 중입니다. 작업이 끝난 뒤 다시 초기화하세요.");
            case "DATA_MISMATCH" -> throw ApiException.conflict(
                    "지급 snapshot, 지갑, 원장 합계가 달라 초기화를 중단했습니다. DB 정합성을 먼저 확인하세요.");
            default -> throw new IllegalStateException("Unexpected local wallet reset result: " + reset.result());
        };
    }

    @Transactional(readOnly = true)
    public WalletResponse getWallet(long userId) {
        try {
            return jdbc.queryForObject("""
                    SELECT u.id AS user_id, u.nickname, w.money_balance, w.point_balance
                    FROM reward.users u
                    JOIN reward.wallets w ON w.user_id = u.id
                    WHERE u.id = ?
                    """, (resultSet, rowNum) -> new WalletResponse(
                    resultSet.getLong("user_id"),
                    resultSet.getString("nickname"),
                    resultSet.getLong("money_balance"),
                    resultSet.getLong("point_balance")), userId);
        } catch (EmptyResultDataAccessException exception) {
            throw ApiException.notFound("사용자 지갑을 찾을 수 없습니다.");
        }
    }

    @Transactional(readOnly = true)
    public List<LedgerEntryResponse> getLedger(long userId, Long beforeId, int limit) {
        ensureUser(userId);
        if (limit < 1 || limit > 100) {
            throw ApiException.badRequest("limit은 1부터 100 사이여야 합니다.");
        }
        if (beforeId != null && beforeId < 1) {
            throw ApiException.badRequest("beforeId는 양수여야 합니다.");
        }

        String cursorClause = beforeId == null ? "" : "AND l.id < ?";
        String sql = """
                SELECT l.id, l.source_type, l.reward_grant_id, l.compensation_id,
                       COALESCE(g.event_id, c.event_id) AS event_id,
                       e.name AS event_name, c.reason,
                       l.currency_type, l.amount, l.created_at
                FROM reward.wallet_ledger l
                LEFT JOIN reward.reward_grants g ON g.id = l.reward_grant_id
                LEFT JOIN reward.compensations c ON c.id = l.compensation_id
                JOIN reward.events e ON e.id = COALESCE(g.event_id, c.event_id)
                WHERE l.user_id = ?
                """ + cursorClause + """
                ORDER BY l.id DESC
                LIMIT ?
                """;
        if (beforeId == null) {
            return jdbc.query(sql, RewardService::mapLedger, userId, limit);
        }
        return jdbc.query(sql, RewardService::mapLedger, userId, beforeId, limit);
    }

    @Transactional(readOnly = true)
    public List<PayoutEntryResponse> searchPayouts(
            Long userId,
            String sourceType,
            Instant from,
            Instant to,
            Long beforeId,
            int limit
    ) {
        if (userId != null && userId < 1) {
            throw ApiException.badRequest("userId는 양수여야 합니다.");
        }
        if (beforeId != null && beforeId < 1) {
            throw ApiException.badRequest("beforeId는 양수여야 합니다.");
        }
        if (limit < 1 || limit > 100) {
            throw ApiException.badRequest("limit은 1부터 100 사이여야 합니다.");
        }
        if (from != null && to != null && !from.isBefore(to)) {
            throw ApiException.badRequest("from은 to보다 이른 시각이어야 합니다.");
        }

        String normalizedSource = null;
        if (sourceType != null && !sourceType.isBlank()) {
            normalizedSource = sourceType.trim().toUpperCase(Locale.ROOT);
            if (!List.of("EVENT", "COMPENSATION").contains(normalizedSource)) {
                throw ApiException.badRequest("sourceType은 EVENT 또는 COMPENSATION이어야 합니다.");
            }
        }

        StringBuilder sql = new StringBuilder("""
                SELECT l.id, l.user_id, l.source_type, l.reward_grant_id, l.compensation_id,
                       COALESCE(g.event_id, c.event_id) AS event_id,
                       e.name AS event_name, c.reason,
                       l.currency_type, l.amount, l.created_at
                FROM reward.wallet_ledger l
                LEFT JOIN reward.reward_grants g ON g.id = l.reward_grant_id
                LEFT JOIN reward.compensations c ON c.id = l.compensation_id
                JOIN reward.events e ON e.id = COALESCE(g.event_id, c.event_id)
                WHERE 1 = 1
                """);
        List<Object> parameters = new ArrayList<>();
        if (userId != null) {
            sql.append(" AND l.user_id = ?");
            parameters.add(userId);
        }
        // The validated enum literal lets PostgreSQL prove a partial-index predicate.
        // Keeping both LEFT JOINs preserves the same response shape for both sources.
        if (normalizedSource != null) {
            sql.append(" AND l.source_type = '").append(normalizedSource).append("'");
        }
        if (from != null) {
            sql.append(" AND l.created_at >= ?");
            parameters.add(Timestamp.from(from));
        }
        if (to != null) {
            sql.append(" AND l.created_at < ?");
            parameters.add(Timestamp.from(to));
        }
        if (beforeId != null) {
            sql.append(" AND l.id < ?");
            parameters.add(beforeId);
        }
        sql.append(" ORDER BY l.id DESC LIMIT ?");
        parameters.add(limit);
        return jdbc.query(sql.toString(), (resultSet, rowNum) -> new PayoutEntryResponse(
                resultSet.getLong("id"),
                resultSet.getLong("user_id"),
                resultSet.getString("source_type"),
                nullableLong(resultSet, "reward_grant_id"),
                nullableLong(resultSet, "compensation_id"),
                resultSet.getLong("event_id"),
                resultSet.getString("event_name"),
                resultSet.getString("reason"),
                resultSet.getString("currency_type"),
                resultSet.getLong("amount"),
                resultSet.getTimestamp("created_at").toInstant()
        ), parameters.toArray());
    }

    private void ensureUser(long userId) {
        Boolean exists = jdbc.queryForObject(
                "SELECT EXISTS (SELECT 1 FROM reward.users WHERE id = ?)", Boolean.class, userId);
        if (!Boolean.TRUE.equals(exists)) {
            throw ApiException.notFound("데모 사용자를 찾을 수 없습니다.");
        }
    }

    private java.util.Optional<EventResponse> findEvent(long userId, long eventId) {
        List<EventResponse> events = jdbc.query(EVENT_SELECT + "WHERE e.id = ?", EVENT_ROW_MAPPER, userId, eventId);
        return events.stream().findFirst();
    }

    private java.util.Optional<EventResponse> findEventForUpdate(long userId, long eventId) {
        List<EventResponse> events = jdbc.query(
                EVENT_SELECT + "WHERE e.id = ? FOR UPDATE OF e", EVENT_ROW_MAPPER, userId, eventId);
        return events.stream().findFirst();
    }

    private static void validateCreateEvent(CreateEventRequest request, String mode) {
        if (!"CLAIM".equals(mode) && !"BATCH".equals(mode)) {
            throw ApiException.badRequest("mode는 CLAIM 또는 BATCH여야 합니다.");
        }
        if (!request.startsAt().isBefore(request.endsAt())) {
            throw ApiException.badRequest("이벤트 종료 시각은 시작 시각보다 늦어야 합니다.");
        }
        if (request.rewardMoney() == 0 && request.rewardPoint() == 0) {
            throw ApiException.badRequest("Money 또는 Point 보상 중 하나는 0보다 커야 합니다.");
        }
        if ("BATCH".equals(mode)) {
            if (request.scheduledAt() == null) {
                throw ApiException.badRequest("BATCH 이벤트에는 예약 시각이 필요합니다.");
            }
            if (request.scheduledAt().isBefore(request.startsAt())
                    || !request.scheduledAt().isBefore(request.endsAt())) {
                throw ApiException.badRequest("예약 시각은 이벤트 기간 안에 있어야 합니다.");
            }
        }
    }

    private static String normalizeStatusFilter(String status) {
        if (status == null || status.isBlank()) {
            return null;
        }
        String normalized = status.trim().toUpperCase(Locale.ROOT);
        if (!List.of("DRAFT", "OPEN", "CLOSED").contains(normalized)) {
            throw ApiException.badRequest("status는 DRAFT, OPEN, CLOSED 중 하나여야 합니다.");
        }
        return normalized;
    }

    private static EventResponse mapEvent(ResultSet resultSet, int rowNum) throws SQLException {
        Timestamp scheduledAt = resultSet.getTimestamp("scheduled_at");
        return new EventResponse(
                resultSet.getLong("id"),
                resultSet.getString("name"),
                resultSet.getString("mode"),
                resultSet.getString("status"),
                resultSet.getTimestamp("starts_at").toInstant(),
                resultSet.getTimestamp("ends_at").toInstant(),
                scheduledAt == null ? null : scheduledAt.toInstant(),
                resultSet.getLong("reward_money"),
                resultSet.getLong("reward_point"),
                resultSet.getInt("recipient_count"),
                resultSet.getBoolean("claimed")
        );
    }

    private static LedgerEntryResponse mapLedger(ResultSet resultSet, int rowNum) throws SQLException {
        return new LedgerEntryResponse(
                resultSet.getLong("id"),
                resultSet.getString("source_type"),
                nullableLong(resultSet, "reward_grant_id"),
                nullableLong(resultSet, "compensation_id"),
                resultSet.getLong("event_id"),
                resultSet.getString("event_name"),
                resultSet.getString("reason"),
                resultSet.getString("currency_type"),
                resultSet.getLong("amount"),
                resultSet.getTimestamp("created_at").toInstant()
        );
    }

    private static Long nullableLong(ResultSet resultSet, String column) throws SQLException {
        Object value = resultSet.getObject(column);
        return value == null ? null : ((Number) value).longValue();
    }

}
