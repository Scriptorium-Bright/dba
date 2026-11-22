package dev.reward.service;

import dev.reward.api.ApiException;
import dev.reward.api.ApiModels.CompensationRequest;
import dev.reward.api.ApiModels.CompensationResponse;
import org.springframework.jdbc.core.ConnectionCallback;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.stereotype.Component;

import java.sql.CallableStatement;
import java.sql.Types;

/** P3 CS 보상의 원자적 기록과 결과 분류를 PostgreSQL procedure에 위임한다. */
@Component
public class RewardCompensationProcedure {
    private final JdbcTemplate jdbc;

    public RewardCompensationProcedure(JdbcTemplate jdbc) {
        this.jdbc = jdbc;
    }

    /** caller-owned Spring 트랜잭션 안에서 procedure를 실행해 오류 시 wallet/ledger도 함께 롤백한다. */
    public CompensationResponse call(long operatorUserId, CompensationRequest request) {
        return jdbc.execute((ConnectionCallback<CompensationResponse>) connection -> {
            try (CallableStatement statement = connection.prepareCall(
                    "{ call reward.grant_compensation(?, ?, ?, ?, ?, ?, ?, ?, ?, ?) }")) {
                statement.setLong(1, request.userId());
                statement.setLong(2, request.eventId());
                statement.setObject(3, request.requestId());
                statement.setString(4, request.incidentReference().trim());
                statement.setString(5, request.reason().trim());
                statement.setLong(6, operatorUserId);
                statement.setLong(7, request.moneyAmount());
                statement.setLong(8, request.pointAmount());
                statement.setNull(9, Types.VARCHAR);
                statement.registerOutParameter(9, Types.VARCHAR);
                statement.setNull(10, Types.BIGINT);
                statement.registerOutParameter(10, Types.BIGINT);
                statement.execute();

                String result = statement.getString(9);
                long rawId = statement.getLong(10);
                Long compensationId = statement.wasNull() ? null : rawId;
                return responseFor(result, compensationId);
            }
        });
    }

    private static CompensationResponse responseFor(String result, Long compensationId) {
        return switch (result) {
            case "PAID" -> new CompensationResponse(result, compensationId, "장애 보상을 지급했습니다.");
            case "REPLAYED" -> new CompensationResponse(result, compensationId, "기존 보상 요청 결과를 확인했습니다.");
            case "ALREADY_COMPENSATED" -> new CompensationResponse(
                    result, compensationId, "같은 장애 건에 대한 보상이 이미 기록되어 있습니다.");
            case "REQUEST_ID_CONFLICT" -> throw ApiException.conflict(
                    "이미 다른 보상 내용에 사용된 요청 ID입니다.");
            default -> throw new IllegalStateException("Unexpected compensation procedure result: " + result);
        };
    }
}
