package dev.reward.api;

import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import javax.sql.DataSource;
import java.sql.Connection;
import java.sql.SQLException;
import java.sql.SQLTransientConnectionException;
import java.util.Map;

/**
 * Controlled API latency scenarios for a separate loopback-only disposable database.
 * These routes are absent unless APP_LATENCY_LAB_ENABLED=true is explicitly set.
 */
@RestController
@RequestMapping("/api/diagnostics")
@ConditionalOnProperty(prefix = "app.latency-lab", name = "enabled", havingValue = "true")
public class ApiLatencyDiagnosticsController {
    private static final String SEQUENTIAL_SCAN_SQL = """
            SELECT count(*)
            FROM diagnostics.scan_probe
            WHERE marker LIKE '%needle%'
            """;

    private final DataSource dataSource;
    private final JdbcTemplate jdbc;

    public ApiLatencyDiagnosticsController(DataSource dataSource, JdbcTemplate jdbc) {
        this.dataSource = dataSource;
        this.jdbc = jdbc;
    }

    /** Returns the database identity seen by this API's own JDBC connection. */
    @GetMapping("/identity")
    public Map<String, Object> databaseIdentity() {
        return jdbc.queryForMap("""
                SELECT current_database() AS database_name,
                       current_setting('server_version') AS server_version,
                       current_user AS database_user,
                       current_setting('application_name') AS application_name
                """);
    }

    /** Holds a pooled connection without issuing SQL to distinguish pool wait from DB work. */
    @GetMapping("/hold-connection")
    public ResponseEntity<Map<String, Object>> holdConnection(
            @RequestParam(defaultValue = "2000") int millis
    ) {
        if (millis < 100 || millis > 5000) {
            throw ApiException.badRequest("대기 시간은 100~5000ms 범위여야 합니다.");
        }

        long startedAt = System.nanoTime();
        try (Connection ignored = dataSource.getConnection()) {
            Thread.sleep(millis);
            long elapsedMillis = (System.nanoTime() - startedAt) / 1_000_000;
            return ResponseEntity.ok(Map.of(
                    "scenario", "connection-pool-wait",
                    "requestedMillis", millis,
                    "elapsedMillis", elapsedMillis));
        } catch (InterruptedException exception) {
            Thread.currentThread().interrupt();
            throw new IllegalStateException("connection-hold request was interrupted", exception);
        } catch (SQLTransientConnectionException exception) {
            return ResponseEntity.status(HttpStatus.SERVICE_UNAVAILABLE).body(Map.of(
                    "reason", "connection_pool_acquisition_timeout",
                    "requestedMillis", millis));
        } catch (SQLException exception) {
            throw new IllegalStateException("could not borrow a diagnostic DB connection", exception);
        }
    }

    /** Updates one isolated probe row; an external transaction can hold its row lock. */
    @PostMapping("/lock-probe")
    public Map<String, Integer> updateLockProbe() {
        int updated = jdbc.update("""
                UPDATE diagnostics.lock_probe
                SET value = value + 1
                WHERE id = 1
                """);
        return Map.of("rowsUpdated", updated);
    }

    /** Executes a fixed unindexed predicate whose exact plan is captured separately. */
    @GetMapping("/sequential-scan")
    public Map<String, Long> sequentialScan() {
        Long matchingRows = jdbc.queryForObject(SEQUENTIAL_SCAN_SQL, Long.class);
        return Map.of("matchingRows", matchingRows == null ? 0L : matchingRows);
    }
}
