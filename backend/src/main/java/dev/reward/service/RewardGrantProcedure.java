package dev.reward.service;

import dev.reward.api.ApiException;
import dev.reward.api.ApiModels.ClaimResponse;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.sql.CallableStatement;
import java.sql.Types;
import java.util.UUID;

/** CLAIM API와 BATCH worker가 공용 PostgreSQL 지급 프로시저를 호출한다. */
@Component
public class RewardGrantProcedure {
    private final JdbcTemplate jdbc;

    public RewardGrantProcedure(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** 현재 Spring 트랜잭션을 그대로 사용하므로 CALL 안의 변경은 호출자와 함께 커밋/롤백된다. */
    public ClaimResponse call(long eventId, long userId, UUID requestId, String trigger) {
        return jdbc.execute((ConnectionCallback<ClaimResponse>) connection -> {
            try (CallableStatement statement = connection.prepareCall(
                    "{ call reward.grant_event_reward(?, ?, ?, ?, ?, ?) }")) {
                statement.setLong(1, eventId);
                statement.setLong(2, userId);
                statement.setObject(3, requestId);
                statement.setString(4, trigger);
                // PostgreSQL INOUT 인자는 입력값도 지정하고 출력 타입도 등록해야 한다.
                statement.setNull(5, Types.VARCHAR);
                statement.registerOutParameter(5, Types.VARCHAR);
                statement.setNull(6, Types.BIGINT);
                statement.registerOutParameter(6, Types.BIGINT);
                statement.execute();

                String result = statement.getString(5);
                long rawGrantId = statement.getLong(6);
                Long grantId = statement.wasNull() ? null : rawGrantId;
                return responseFor(result, grantId);
            }
        });
    }

    private static ClaimResponse responseFor(String result, Long grantId) {
        return switch (result) {
            case "PAID" -> new ClaimResponse(result, grantId, "보상을 지급했습니다.");
            case "REPLAYED" -> new ClaimResponse(result, grantId, "기존 지급 결과를 확인했습니다.");
            case "ALREADY_GRANTED" -> new ClaimResponse(result, grantId, "이미 이 이벤트 보상을 받았습니다.");
            case "NOT_AVAILABLE" -> new ClaimResponse(result, null, "현재 수령할 수 없는 이벤트입니다.");
            case "EVENT_NOT_FOUND" -> throw ApiException.notFound("이벤트를 찾을 수 없습니다.");
            case "REQUEST_ID_CONFLICT" -> throw ApiException.conflict(
                    "이미 다른 지급 처리에 사용된 요청 ID입니다.");
            default -> throw new IllegalStateException("Unexpected grant procedure result: " + result);
        };
    }
}
