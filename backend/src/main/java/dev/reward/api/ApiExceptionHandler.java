package dev.reward.api;

import dev.reward.api.ApiModels.ApiError;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingRequestHeaderException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;

@RestControllerAdvice
public class ApiExceptionHandler {
    @ExceptionHandler(ApiException.class)
    public ResponseEntity<ApiError> handleApiException(ApiException exception) {
        return ResponseEntity.status(exception.status())
                .body(new ApiError(exception.code(), exception.getMessage()));
    }

    @ExceptionHandler(MethodArgumentNotValidException.class)
    public ResponseEntity<ApiError> handleValidation(MethodArgumentNotValidException exception) {
        String message = exception.getBindingResult().getFieldErrors().stream()
                .findFirst()
                .map(error -> error.getField() + " 값을 확인해 주세요.")
                .orElse("입력값을 확인해 주세요.");
        return ResponseEntity.badRequest().body(new ApiError("VALIDATION_ERROR", message));
    }

    @ExceptionHandler(HttpMessageNotReadableException.class)
    public ResponseEntity<ApiError> handleUnreadableBody() {
        return ResponseEntity.badRequest()
                .body(new ApiError("INVALID_BODY", "요청 형식 또는 날짜 값을 확인해 주세요."));
    }

    @ExceptionHandler(MissingRequestHeaderException.class)
    public ResponseEntity<ApiError> handleMissingHeader(MissingRequestHeaderException exception) {
        return ResponseEntity.badRequest()
                .body(new ApiError("MISSING_HEADER", exception.getHeaderName() + " 헤더가 필요합니다."));
    }

    @ExceptionHandler(DataAccessException.class)
    public ResponseEntity<ApiError> handleDatabaseFailure() {
        return ResponseEntity.status(HttpStatus.INTERNAL_SERVER_ERROR)
                .body(new ApiError("DATABASE_ERROR", "DB 요청을 처리하지 못했습니다. 연결 상태를 확인해 주세요."));
    }
}
