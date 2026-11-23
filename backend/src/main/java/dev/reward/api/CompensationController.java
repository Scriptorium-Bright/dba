package dev.reward.api;

import dev.reward.api.ApiModels.CompensationRequest;
import dev.reward.api.ApiModels.CompensationResponse;
import dev.reward.service.RewardService;
import jakarta.validation.Valid;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Locale;

/** P3 로컬 실습용 CS 보상 endpoint. 기능 플래그와 데모 ADMIN 헤더를 모두 확인한다. */
@RestController
@RequestMapping("/api/admin/compensations")
@ConditionalOnProperty(prefix = "app", name = "p3-compensation-enabled", havingValue = "true")
public class CompensationController {
    private final RewardService rewardService;

    public CompensationController(RewardService rewardService) {
        this.rewardService = rewardService;
    }

    @PostMapping
    public CompensationResponse compensate(
            @RequestHeader(value = "X-User-Id", defaultValue = "101") long operatorUserId,
            @RequestHeader(value = "X-Demo-Role", defaultValue = "USER") String demoRole,
            @Valid @RequestBody CompensationRequest request
    ) {
        if (!"ADMIN".equals(demoRole.toUpperCase(Locale.ROOT))) {
            throw ApiException.forbidden();
        }
        return rewardService.compensate(operatorUserId, request);
    }
}
