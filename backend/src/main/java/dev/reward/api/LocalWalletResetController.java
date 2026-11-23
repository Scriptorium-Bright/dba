package dev.reward.api;

import dev.reward.api.ApiModels.WalletResetResponse;
import dev.reward.service.RewardService;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestHeader;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

import java.util.Locale;

/**
 * 로컬 데모의 지갑 초기화 도구다.
 * 설정이 켜진 개발 Compose에서만 등록되며, 실제 운영자 보상 취소 API가 아니다.
 */
@RestController
@RequestMapping("/api")
@ConditionalOnProperty(prefix = "app", name = "demo-wallet-reset-enabled", havingValue = "true")
public class LocalWalletResetController {
    private final RewardService rewardService;

    public LocalWalletResetController(RewardService rewardService) {
        this.rewardService = rewardService;
    }

    /** 현재 데모 사용자 하나의 wallet, grant, ledger를 함께 초기화한다. */
    @PostMapping("/me/wallet/reset")
    public WalletResetResponse resetMyWallet(
            @RequestHeader(value = "X-User-Id", defaultValue = "101") long userId
    ) {
        return rewardService.resetLocalUserWallet(userId);
    }

    /** 운영 화면에서 로컬 DB의 전체 사용자 지갑과 지급 이력을 초기화한다. */
    @PostMapping("/admin/wallets/reset-all")
    public WalletResetResponse resetAllWallets(
            @RequestHeader(value = "X-Demo-Role", defaultValue = "USER") String demoRole
    ) {
        requireAdmin(demoRole);
        return rewardService.resetAllLocalWallets();
    }

    private static void requireAdmin(String role) {
        if (!"ADMIN".equals(role.toUpperCase(Locale.ROOT))) {
            throw ApiException.forbidden();
        }
    }
}
