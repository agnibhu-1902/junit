package com.example.HelloWorldApplication;

import org.junit.jupiter.api.Test;
import org.springframework.boot.test.context.SpringBootTest;

import static org.assertj.core.api.Assertions.assertThat;

@SpringBootTest
public class HelloWorldApplicationTest {

    @Test
    void contextLoads() {
        // Verifies the Spring application context starts without errors
    }

    @Test
    void mainMethodRunsWithoutException() {
        // Verify main() can be called without throwing
        HelloWorldApplication.main(new String[]{});
    }
}
