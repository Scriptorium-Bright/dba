package dev.reward.api;

import jakarta.validation.constraints.Min;
import jakarta.validation.constraints.NotBlank;
import jakarta.validation.constraints.NotNull;
import jakarta.validation.constraints.Positive;
import jakarta.validation.constraints.Size;

import java.time.Instant;
import java.util.List;
import java.util.UUID;

public final class ApiModels {
    private ApiModels() {}

    public record EventResponse(
            long id,
            String name,
            String mode,
            String status,
            Instant startsAt,
            Instant endsAt,
            Instant scheduledAt,
            long rewardMoney,
            long rewardPoint,
            int recipientCount,
            boolean claimed
    ) {}

    public record CreateEventRequest(
            @NotBlank @Size(max = 100) String name,
            @NotBlank String mode,
            @NotNull Instant startsAt,
            @NotNull Instant endsAt,
            Instant scheduledAt,
            @NotNull @Min(0) Long rewardMoney,
            @NotNull @Min(0) Long rewardPoint
    ) {}

    public record WalletResponse(
            long userId,
            String nickname,
            long moneyBalance,
            long pointBalance
    ) {}

    public record LedgerEntryResponse(
            long id,
            String sourceType,
            Long rewardGrantId,
            Long compensationId,
            long eventId,
            String eventName,
            String reason,
            String currencyType,
            long amount,
            Instant createdAt
    ) {}

    /** 운영자가 사용자/기간/출처 조건으로 찾는 통합 지급 원장 행이다. */
    public record PayoutEntryResponse(
            long id,
            long userId,
            String sourceType,
            Long rewardGrantId,
            Long compensationId,
            long eventId,
            String eventName,
            String reason,
            String currencyType,
            long amount,
            Instant createdAt
    ) {}

    public record ClaimResponse(String result, Long grantId, String message) {}

    /** 로컬 운영자가 한 장애 건에 대해 사용자 1명의 CS 보상을 기록한다. */
    public record CompensationRequest(
            @NotNull @Positive Long userId,
            @NotNull @Positive Long eventId,
            @NotNull UUID requestId,
            @NotBlank @Size(max = 100) String incidentReference,
            @NotBlank @Size(max = 500) String reason,
            @NotNull @Min(0) Long moneyAmount,
            @NotNull @Min(0) Long pointAmount
    ) {}

    public record CompensationResponse(String result, Long compensationId, String message) {}

    /** 로컬 데모의 사용자 지갑/지급 이력 초기화 결과다. */
    public record WalletResetResponse(
            long usersReset,
            long grantsDeleted,
            long compensationsDeleted,
            long ledgerRowsDeleted,
            String message
    ) {}

    public record RecipientUpdateRequest(
            @NotNull @Size(max = 10000) List<@NotNull @Positive Long> userIds
    ) {}

    public record RecipientUpdateResponse(long eventId, int recipientCount) {}

    public record BatchRunRequest(@Min(1) @jakarta.validation.constraints.Max(1000) Integer maxRecipients) {}

    public record BatchRunResponse(
            long id,
            long eventId,
            String status,
            int targetCount,
            int paidCount,
            int replayedCount,
            int alreadyGrantedCount,
            int notAvailableCount,
            int failedCount,
            Instant startedAt,
            Instant finishedAt,
            String errorSummary
    ) {}

    public record BatchDistributionResponse(
            long eventId,
            String status,
            Instant scheduledAt,
            int recipientCount,
            int grantedCount,
            int pendingCount,
            List<Long> recipientUserIds,
            List<BatchRunResponse> recentRuns
    ) {}

    public record ApiError(String code, String message) {}
}
