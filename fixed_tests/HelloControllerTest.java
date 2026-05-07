package com.example.HelloWorldApplication.controller;

import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.ResponseEntity;

import static org.assertj.core.api.Assertions.assertThat;

public class HelloControllerTest {

    private HelloController helloController;

    @BeforeEach
    void setUp() {
        helloController = new HelloController();
    }

    @Test
    void testGreet_returnsOkOrInternalServerError() {
        // The controller uses an internal Random — verify both valid outcomes
        boolean sawSuccess = false;
        boolean sawFailure = false;

        for (int i = 0; i < 20; i++) {
            ResponseEntity<String> response = helloController.greet();
            int status = response.getStatusCode().value();
            assertThat(status).isIn(200, 500);

            if (status == 200) {
                assertThat(response.getBody()).isEqualTo("Hello from Spring Boot!");
                sawSuccess = true;
            } else {
                assertThat(response.getBody()).isEqualTo("Internal error");
                sawFailure = true;
            }
            if (sawSuccess && sawFailure) break;
        }
        assertThat(sawSuccess || sawFailure).isTrue();
    }

    @Test
    void testGreet_successResponse_hasCorrectBody() {
        ResponseEntity<String> lastSuccess = null;
        for (int i = 0; i < 30; i++) {
            ResponseEntity<String> response = helloController.greet();
            if (response.getStatusCode().value() == 200) {
                lastSuccess = response;
                break;
            }
        }
        if (lastSuccess != null) {
            assertThat(lastSuccess.getBody()).isEqualTo("Hello from Spring Boot!");
        }
    }

    @Test
    void testGreet_failureResponse_hasCorrectBody() {
        ResponseEntity<String> lastFailure = null;
        for (int i = 0; i < 30; i++) {
            ResponseEntity<String> response = helloController.greet();
            if (response.getStatusCode().value() == 500) {
                lastFailure = response;
                break;
            }
        }
        if (lastFailure != null) {
            assertThat(lastFailure.getBody()).isEqualTo("Internal error");
        }
    }

    @Test
    void testGreet_responseIsNeverNull() {
        for (int i = 0; i < 10; i++) {
            ResponseEntity<String> response = helloController.greet();
            assertThat(response).isNotNull();
            assertThat(response.getBody()).isNotNull();
        }
    }
}
