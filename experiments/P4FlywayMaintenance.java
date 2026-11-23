import java.net.URI;
import java.sql.DriverManager;
import java.util.Map;
import java.util.Set;
import org.flywaydb.core.Flyway;

/**
 * Local-only Flyway control for reproducing the P4 V15 failed-migration recovery procedure.
 *
 * Run from the repository root with Flyway and PostgreSQL JDBC jars on the classpath. The
 * default URL points at the disposable P4 recovery database; override connection values with
 * P4_FLYWAY_URL, P4_DB_MIGRATION_USER, and P4_DB_MIGRATION_PASSWORD when needed.
 */
public final class P4FlywayMaintenance {
    private static final String DEFAULT_URL =
            "jdbc:postgresql://127.0.0.1:55433/rewardp4_flyway_recovery_probe?currentSchema=reward";
    private static final Set<String> ALLOWED_DATABASES = Set.of(
            "rewardp4_flyway_recovery_probe", "rewardp4_flyway_populated_probe");

    private P4FlywayMaintenance() {}

    public static void main(String[] args) {
        if (args.length < 1 || args.length > 2) {
            throw new IllegalArgumentException("usage: P4FlywayMaintenance migrate|repair [target-version]");
        }

        String url = setting("P4_FLYWAY_URL", DEFAULT_URL);
        String user = setting("P4_DB_MIGRATION_USER", "reward_migration");
        String password = setting("P4_DB_MIGRATION_PASSWORD", "p4_migration_local_only");
        String expectedDatabase = validateLocalProbeUrl(url);
        validateConnectedDatabase(url, user, password, expectedDatabase);

        var configuration = Flyway.configure()
                .dataSource(url, user, password)
                // These are the same shared and P4-only locations used by compose.p4.yaml.
                .locations("filesystem:database/migrations", "filesystem:database/p4-migrations")
                .defaultSchema("reward")
                .schemas("reward")
                // V15 uses CREATE INDEX CONCURRENTLY and its companion .conf disables transactions.
                .configuration(Map.of("flyway.postgresql.transactional.lock", "false"));

        if (args.length == 2) configuration.target(args[1]);
        Flyway flyway = configuration.load();

        switch (args[0]) {
            case "migrate" -> System.out.println("migrationsExecuted=" + flyway.migrate().migrationsExecuted);
            case "repair" -> {
                flyway.repair();
                System.out.println("repair=complete");
            }
            default -> throw new IllegalArgumentException("action must be migrate or repair");
        }
    }

    private static String validateLocalProbeUrl(String url) {
        final URI uri;
        try {
            if (!url.startsWith("jdbc:")) throw new IllegalArgumentException("expected a JDBC URL");
            uri = URI.create(url.substring("jdbc:".length()));
        } catch (IllegalArgumentException error) {
            throw new IllegalArgumentException("rejected P4 Flyway URL: " + error.getMessage(), error);
        }

        String database = uri.getPath() == null ? "" : uri.getPath().replaceFirst("^/", "");
        if (!"postgresql".equals(uri.getScheme())
                || !Set.of("127.0.0.1", "localhost").contains(uri.getHost())
                || uri.getPort() != 55433
                || !ALLOWED_DATABASES.contains(database)) {
            throw new IllegalArgumentException(
                    "rejected P4 Flyway target; allowed targets are the two named databases on loopback port 55433");
        }
        return database;
    }

    private static void validateConnectedDatabase(
            String url, String user, String password, String expectedDatabase
    ) {
        try (var connection = DriverManager.getConnection(url, user, password);
             var statement = connection.createStatement();
             var result = statement.executeQuery("SELECT current_database()")) {
            if (!result.next() || !expectedDatabase.equals(result.getString(1))) {
                throw new IllegalStateException(
                        "connected database differs from the validated probe target: " + expectedDatabase);
            }
        } catch (java.sql.SQLException error) {
            throw new IllegalStateException("could not confirm the local disposable Flyway database", error);
        }
    }

    private static String setting(String name, String fallback) {
        String value = System.getenv(name);
        return value == null || value.isBlank() ? fallback : value;
    }
}
