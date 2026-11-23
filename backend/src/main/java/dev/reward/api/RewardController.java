package dev.reward.api;

import dev.reward.api.ApiModels.ClaimResponse;
import dev.reward.api.ApiModels.BatchDistributionResponse;
import dev.reward.api.ApiModels.BatchRunRequest;
import dev.reward.api.ApiModels.BatchRunResponse;
import dev.reward.api.ApiModels.CreateEventRequest;
import dev.reward.api.ApiModels.EventResponse;
import dev.reward.api.ApiModels.LedgerEntryResponse;
import dev.reward.api.ApiModels.RecipientUpdateRequest;
import dev.reward.api.ApiModels.RecipientUpdateResponse;
import dev.reward.api.ApiModels.WalletResponse;
import dev.reward.service.BatchService;
import dev.reward.service.RewardService;
import jakarta.validation.Valid;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.PutMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.util.List;
import java.util.Locale;
import java.util.UUID;

@RestController
@RequestMapping("/api")
public class RewardController {
    private final RewardService rewardService;
    private final BatchService batchService;

    public RewardController(RewardService rewardService, BatchService batchService) {
        this.rewardService = rewardService;
        this.batchService = batchService;
    }

    @GetMapping("/events")
    public List<EventResponse> listEvents(
            @RequestHeader(value = "X-User-Id", defaultValue = "101") long userId,
            @RequestParam(required = false) String status
    ) {
        return rewardService.listEvents(userId, status);
    }

    @GetMapping("/events/{eventId}")
    public EventResponse getEvent(
            @PathVariable long eventId,
            @RequestHeader(value = "X-User-Id", defaultValue = "101") long userId
    ) {
        return rewardService.getEvent(userId, eventId);
    }

    @PostMapping("/admin/events")
    public EventResponse createEvent(
            @RequestHeader(value = "X-User-Id", defaultValue = "101") long userId,
            @RequestHeader(value = "X-Demo-Role", defaultValue = "USER") String demoRole,
            @Valid @RequestBody CreateEventRequest request
    ) {
        requireAdmin(demoRole);
        return rewardService.createEvent(userId, request);
    }

    @PostMapping("/admin/events/{eventId}/open")
    public EventResponse openEvent(
            @PathVariable long eventId,
            @RequestHeader(value = "X-User-Id", defaultValue = "101") long userId,
            @RequestHeader(value = "X-Demo-Role", defaultValue = "USER") String demoRole
    ) {
        requireAdmin(demoRole);
        return rewardService.changeEventStatus(userId, eventId, "OPEN");
    }

    @PostMapping("/admin/events/{eventId}/close")
    public EventResponse closeEvent(
            @PathVariable long eventId,
            @RequestHeader(value = "X-User-Id", defaultValue = "101") long userId,
            @RequestHeader(value = "X-Demo-Role", defaultValue = "USER") String demoRole
    ) {
        requireAdmin(demoRole);
        return rewardService.changeEventStatus(userId, eventId, "CLOSED");
    }

    @PutMapping("/admin/events/{eventId}/recipients")
    public RecipientUpdateResponse replaceRecipients(
            @PathVariable long eventId,
            @RequestHeader(value = "X-User-Id", defaultValue = "101") long userId,
            @RequestHeader(value = "X-Demo-Role", defaultValue = "USER") String demoRole,
            @Valid @RequestBody RecipientUpdateRequest request
    ) {
        requireAdmin(demoRole);
        return rewardService.replaceBatchRecipients(userId, eventId, request.userIds());
    }

    @GetMapping("/admin/events/{eventId}/distribution")
    public BatchDistributionResponse batchDistribution(
            @PathVariable long eventId,
            @RequestHeader(value = "X-Demo-Role", defaultValue = "USER") String demoRole
    ) {
        requireAdmin(demoRole);
        return batchService.distribution(eventId);
    }

    @PostMapping("/admin/events/{eventId}/run")
    public BatchRunResponse runBatch(
            @PathVariable long eventId,
            @RequestHeader(value = "X-Demo-Role", defaultValue = "USER") String demoRole,
            @Valid @RequestBody(required = false) BatchRunRequest request
    ) {
        requireAdmin(demoRole);
        return batchService.run(eventId, request == null ? null : request.maxRecipients());
    }

    @PostMapping("/events/{eventId}/claims")
    public ClaimResponse claim(
            @PathVariable long eventId,
            @RequestHeader(value = "X-User-Id", defaultValue = "101") long userId,
            @RequestHeader("Idempotency-Key") String idempotencyKey
    ) {
        UUID requestId;
        try {
            requestId = UUID.fromString(idempotencyKey);
        } catch (IllegalArgumentException exception) {
            throw ApiException.badRequest("Idempotency-Key는 UUID 형식이어야 합니다.");
        }
        return rewardService.claim(userId, eventId, requestId);
    }

    @GetMapping("/me/wallet")
    public WalletResponse wallet(
            @RequestHeader(value = "X-User-Id", defaultValue = "101") long userId
    ) {
        return rewardService.getWallet(userId);
    }

    @GetMapping("/me/ledger")
    public List<LedgerEntryResponse> ledger(
            @RequestHeader(value = "X-User-Id", defaultValue = "101") long userId,
            @RequestParam(required = false) Long beforeId,
            @RequestParam(defaultValue = "20") int limit
    ) {
        return rewardService.getLedger(userId, beforeId, limit);
    }

    private static void requireAdmin(String role) {
        if (!"ADMIN".equals(role.toUpperCase(Locale.ROOT))) {
            throw ApiException.forbidden();
        }
    }
}
