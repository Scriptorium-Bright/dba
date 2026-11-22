package dev.reward.api;

import dev.reward.api.ApiModels.PayoutEntryResponse;
import dev.reward.service.RewardService;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.format.annotation.DateTimeFormat;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import java.time.Instant;
import java.util.List;
import java.util.Locale;

/** P3 로컬 운영자가 이벤트/장애 보상의 통합 원장을 검색하는 endpoint다. */
@RestController
@RequestMapping("/api/admin/payouts")
@ConditionalOnProperty(prefix = "app", name = "p3-compensation-enabled", havingValue = "true")
public class AdminPayoutController {
    private final RewardService rewardService;

    public AdminPayoutController(RewardService rewardService) {
        this.rewardService = rewardService;
    }

    @GetMapping
    public List<PayoutEntryResponse> search(
            @RequestHeader(value = "X-Demo-Role", defaultValue = "USER") String demoRole,
            @RequestParam(required = false) Long userId,
            @RequestParam(required = false) String sourceType,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) Instant from,
            @RequestParam(required = false) @DateTimeFormat(iso = DateTimeFormat.ISO.DATE_TIME) Instant to,
            @RequestParam(required = false) Long beforeId,
            @RequestParam(defaultValue = "20") int limit
    ) {
        if (!"ADMIN".equals(demoRole.toUpperCase(Locale.ROOT))) {
            throw ApiException.forbidden();
        }
        return rewardService.searchPayouts(userId, sourceType, from, to, beforeId, limit);
    }
}
