FROM maven:3.9.12-eclipse-temurin-21 AS build
WORKDIR /workspace
COPY backend/pom.xml ./pom.xml
COPY backend/src ./src
COPY database/migrations ./src/main/resources/db/migration
COPY database/p4-migrations ./src/main/resources/db/p4-migration
RUN mvn -B -DskipTests package

FROM eclipse-temurin:21-jre
WORKDIR /app
COPY --from=build /workspace/target/reward-api-0.1.0.jar /app/reward-api.jar
EXPOSE 8080
ENTRYPOINT ["java", "-jar", "/app/reward-api.jar"]
