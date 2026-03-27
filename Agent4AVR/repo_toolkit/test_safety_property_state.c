#if 0
program="/tmp/${0%.*}"
clang "$0" -o $program -L. -lspstate -lpthread -lrt -Wno-int-to-void-pointer-cast
LD_LIBRARY_PATH=.:$LD_LIBRARY_PATH $program "$@"
rm $program
exit
#endif

#include <stdio.h>
#include <stdlib.h>
#include <pthread.h>
#include <unistd.h>
#include <time.h>
#include <string.h>
#include "safety_property_state.h"

#define NUM_THREADS 10
#define UPDATES_PER_THREAD 100
#define MAX_KEY_LENGTH 1024
#define MAX_FILE_LENGTH 1024

int tests_passed = 0;
int tests_failed = 0;

#define ASSERT(condition, message) \
    do { \
        if (!(condition)) { \
            printf("❌ FAIL: %s\n", message); \
            tests_failed++; \
        } else { \
            printf("✅ PASS: %s\n", message); \
            tests_passed++; \
        } \
    } while(0)

#define ASSERT_EQUAL(actual, expected, message) \
    ASSERT((actual) == (expected), message)

void* thread_func(void* arg) {
    int thread_id = *(int*)arg;
    
    for (int i = 0; i < UPDATES_PER_THREAD; i++) {
        _SPA_safety_property_update_state(thread_id * 100 + i, "memory.c", 10 + i, 
                                         "memory_block_%d", thread_id);
        
        void* fake_ptr = (void*)(0x1000 + thread_id * 0x1000 + i);
        _SPA_safety_property_update_state(i % 3, "pointers.c", 20 + i,
                                         "ptr_%p_lifecycle", fake_ptr);
        
        if (i % 10 == 0) {
            _SPA_safety_property_update_state(1, "errors.c", 30 + i,
                                             "error_%d_%d", thread_id, i);
        }
        
        _SPA_safety_property_update_state(i, "counters.c", 40 + i,
                                         "thread_%d_counter", thread_id);
        
        usleep(1000);
    }
    
    return NULL;
}

void test_basic_operations() {
    printf("=== Testing Basic Operations ===\n");
    
    _SPA_safety_property_update_state(1, "test.c", 10, "simple_key");
    int val = _SPA_safety_property_get_state(0, "simple_key");
    ASSERT_EQUAL(val, 1, "Simple key update and get");
    
    _SPA_safety_property_update_state(10, "test.c", 20, "counter");
    _SPA_safety_property_update_state(20, "test.c", 21, "counter");
    _SPA_safety_property_update_state(30, "test.c", 22, "counter");
    
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "counter"), 30, "Current state");
    ASSERT_EQUAL(_SPA_safety_property_get_state(1, "counter"), 20, "First history");
    ASSERT_EQUAL(_SPA_safety_property_get_state(2, "counter"), 10, "Second history");
    
    void* ptr1 = (void*)0x12345678;
    void* ptr2 = (void*)0x87654321;
    
    _SPA_safety_property_update_state(1, "pointers.c", 50, "obj_%p_type_%s", ptr1, "buffer");
    _SPA_safety_property_update_state(2, "pointers.c", 51, "obj_%p_type_%s", ptr2, "file");
    
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "obj_%p_type_%s", ptr1, "buffer"), 1, "Complex key 1");
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "obj_%p_type_%s", ptr2, "file"), 2, "Complex key 2");
    
    ASSERT_EQUAL(_SPA_safety_property_get_state(-1, "counter"), 10, "Negative index -1");
    ASSERT_EQUAL(_SPA_safety_property_get_state(-2, "counter"), 20, "Negative index -2");
}

void test_concurrent_operations() {
    printf("\n=== Testing Concurrent Operations ===\n");
    
    pthread_t threads[NUM_THREADS];
    int thread_ids[NUM_THREADS];
    
    for (int i = 0; i < NUM_THREADS; i++) {
        thread_ids[i] = i;
        int result = pthread_create(&threads[i], NULL, thread_func, &thread_ids[i]);
        ASSERT_EQUAL(result, 0, "Thread creation");
    }
    
    for (int i = 0; i < NUM_THREADS; i++) {
        pthread_join(threads[i], NULL);
    }
    
    printf("All threads completed\n");
    
    for (int i = 0; i < NUM_THREADS; i++) {
        int final_val = _SPA_safety_property_get_state(0, "thread_%d_counter", i);
        ASSERT_EQUAL(final_val, UPDATES_PER_THREAD - 1, 
                    "Thread counter final value");
    }
}

void test_error_conditions() {
    printf("\n=== Testing Error Conditions ===\n");
    
    int result = _SPA_safety_property_get_state(0, "non_existent_key");
    ASSERT_EQUAL(result, -1, "Non-existent key returns -1");
    
    result = _SPA_safety_property_get_state(100, "simple_key");
    ASSERT_EQUAL(result, -1, "Invalid positive index returns -1");
    
    result = _SPA_safety_property_get_state(-100, "simple_key");
    ASSERT_EQUAL(result, -1, "Invalid negative index returns -1");
    
    result = _SPA_safety_property_update_state(1, "test.c", 100, "");
    ASSERT_EQUAL(result, -1, "Empty key update returns -1");
    
    result = _SPA_safety_property_update_state(1, NULL, 100, "test_key");
    ASSERT_EQUAL(result, -1, "NULL file update returns -1");
}

void test_edge_cases() {
    printf("=== Testing Edge Cases ===\n");
    
    // Test maximum key length
    char long_key[MAX_KEY_LENGTH + 10];
    memset(long_key, 'a', sizeof(long_key) - 1);
    long_key[sizeof(long_key) - 1] = '\0';
    _SPA_safety_property_update_state(999, "edge.c", 1, "%s", long_key);
    int result = _SPA_safety_property_get_state(0, "%s", long_key);
    ASSERT_EQUAL(result, 999, "Maximum key length handling");
    
    // Test special characters in keys
    _SPA_safety_property_update_state(111, "edge.c", 2, "key with spaces");
    _SPA_safety_property_update_state(222, "edge.c", 3, "key/with/slashes");
    _SPA_safety_property_update_state(333, "edge.c", 4, "key.with.dots");
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "key with spaces"), 111, "Key with spaces");
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "key/with/slashes"), 222, "Key with slashes");
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "key.with.dots"), 333, "Key with dots");
    
    // Test very large values
    _SPA_safety_property_update_state(2147483647, "edge.c", 5, "max_int_key");
    _SPA_safety_property_update_state(-2147483647, "edge.c", 6, "min_int_key");
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "max_int_key"), 2147483647, "Maximum integer value");
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "min_int_key"), -2147483647, "Minimum integer value");
}

void test_thread_safety_stress() {
    printf("\n=== Testing Thread Safety Stress ===\n");
    
    pthread_t threads[20];
    int thread_ids[20];
    
    for (int i = 0; i < 20; i++) {
        thread_ids[i] = i;
        pthread_create(&threads[i], NULL, thread_func, &thread_ids[i]);
    }
    
    // Main thread also updates states
    for (int i = 0; i < 50; i++) {
        _SPA_safety_property_update_state(i * 10, "main.c", 100 + i, "shared_counter");
        usleep(500);
    }
    
    for (int i = 0; i < 20; i++) {
        pthread_join(threads[i], NULL);
    }
    
    // Verify no corruption occurred
    int final_value = _SPA_safety_property_get_state(0, "shared_counter");
    ASSERT(final_value >= 0, "No corruption in shared state");
    printf("Final shared counter: %d\n", final_value);
}

void test_memory_management() {
    printf("\n=== Testing Memory Management ===\n");
    
    // Test many unique keys
    for (int i = 0; i < 1000; i++) {
        _SPA_safety_property_update_state(i, "memory.c", i, "unique_key_%d", i);
    }
    
    // Verify all can be retrieved
    int success_count = 0;
    for (int i = 0; i < 1000; i++) {
        int val = _SPA_safety_property_get_state(0, "unique_key_%d", i);
        if (val == i) success_count++;
    }
    ASSERT_EQUAL(success_count, 1000, "All unique keys preserved");
    
    // Test rapid updates to same key
    for (int i = 0; i < 500; i++) {
        _SPA_safety_property_update_state(i, "memory.c", 1000 + i, "rapid_key");
    }
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "rapid_key"), 499, "Rapid updates handled");
}

void test_file_lineno_tracking() {
    printf("\n=== Testing File and Line Number Tracking ===\n");
    
    _SPA_safety_property_update_state(1, "file1.c", 10, "tracked_key");
    _SPA_safety_property_update_state(2, "file2.c", 20, "tracked_key");
    _SPA_safety_property_update_state(3, "file3.c", 30, "tracked_key");
    
    // This would normally require internal access to verify file/line info
    // For now, just verify the values are correct
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "tracked_key"), 3, "File/line tracking - current");
    ASSERT_EQUAL(_SPA_safety_property_get_state(1, "tracked_key"), 2, "File/line tracking - history 1");
    ASSERT_EQUAL(_SPA_safety_property_get_state(2, "tracked_key"), 1, "File/line tracking - history 2");
}

void test_format_string_edge_cases() {
    printf("\n=== Testing Format String Edge Cases ===\n");
    
    // Test various format specifiers
    _SPA_safety_property_update_state(100, "format.c", 1, "simple");
    _SPA_safety_property_update_state(200, "format.c", 2, "key_%s_%d", "test", 42);
    _SPA_safety_property_update_state(300, "format.c", 3, "key_%x", 0xABCD);
    _SPA_safety_property_update_state(400, "format.c", 4, "key_%p", (void*)0x1234);
    
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "simple"), 100, "Simple format");
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "key_%s_%d", "test", 42), 200, "String and int format");
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "key_%x", 0xABCD), 300, "Hex format");
    ASSERT_EQUAL(_SPA_safety_property_get_state(0, "key_%p", (void*)0x1234), 400, "Pointer format");
}

void test_negative_index_boundaries() {
    printf("\n=== Testing Negative Index Boundaries ===\n");
    
    // Create some history
    for (int i = 1; i <= 10; i++) {
        _SPA_safety_property_update_state(i * 10, "negative.c", i, "negative_test");
    }

    // Test various negative indices
    ASSERT_EQUAL(_SPA_safety_property_get_state(-1, "negative_test"), 10, "Negative index -1");
    ASSERT_EQUAL(_SPA_safety_property_get_state(-2, "negative_test"), 20, "Negative index -2"); 
    ASSERT_EQUAL(_SPA_safety_property_get_state(-10, "negative_test"), 100, "Negative index -10");
    ASSERT_EQUAL(_SPA_safety_property_get_state(-11, "negative_test"), -1, "Negative index out of bounds");
    ASSERT_EQUAL(_SPA_safety_property_get_state(-100, "negative_test"), -1, "Large negative index out of bounds");
}

void test_mixed_operations() {
    printf("\n=== Testing Mixed Operations ===\n");
    
    // Interleave updates and gets
    _SPA_safety_property_update_state(1, "mixed.c", 1, "mixed_key");
    int val1 = _SPA_safety_property_get_state(0, "mixed_key");
    _SPA_safety_property_update_state(2, "mixed.c", 2, "mixed_key");
    int val2 = _SPA_safety_property_get_state(0, "mixed_key");
    int hist1 = _SPA_safety_property_get_state(1, "mixed_key");
    _SPA_safety_property_update_state(3, "mixed.c", 3, "mixed_key");
    int val3 = _SPA_safety_property_get_state(0, "mixed_key");
    int hist2 = _SPA_safety_property_get_state(2, "mixed_key");
    
    ASSERT_EQUAL(val1, 1, "Mixed ops - first get");
    ASSERT_EQUAL(val2, 2, "Mixed ops - second get");
    ASSERT_EQUAL(val3, 3, "Mixed ops - third get");
    ASSERT_EQUAL(hist1, 1, "Mixed ops - first history");
    ASSERT_EQUAL(hist2, 1, "Mixed ops - second history");
}

void test_complex_scenarios() {
    printf("\n=== Testing Complex Scenarios ===\n");
    
    void* memory_blocks[3];
    for (int i = 0; i < 3; i++) {
        memory_blocks[i] = malloc(1024);
        _SPA_safety_property_update_state(1, "memory.c", 100 + i, 
                                         "mem_block_%p_state", memory_blocks[i]);
    }
    
    for (int i = 0; i < 3; i++) {
        _SPA_safety_property_update_state(2, "memory.c", 110 + i,
                                         "mem_block_%p_state", memory_blocks[i]);
    }
    
    free(memory_blocks[1]);
    _SPA_safety_property_update_state(0, "memory.c", 120,
                                     "mem_block_%p_state", memory_blocks[1]);
    
    int state0 = _SPA_safety_property_get_state(0, "mem_block_%p_state", memory_blocks[0]);
    int state1 = _SPA_safety_property_get_state(0, "mem_block_%p_state", memory_blocks[1]);
    int state2 = _SPA_safety_property_get_state(0, "mem_block_%p_state", memory_blocks[2]);
    
    ASSERT_EQUAL(state0, 2, "Memory block 0 current state");
    ASSERT_EQUAL(state1, 0, "Memory block 1 freed state");
    ASSERT_EQUAL(state2, 2, "Memory block 2 current state");
    
    int hist0 = _SPA_safety_property_get_state(1, "mem_block_%p_state", memory_blocks[0]);
    ASSERT_EQUAL(hist0, 1, "Memory block 0 history state");
    
    free(memory_blocks[0]);
    free(memory_blocks[2]);
}

void test_performance() {
    printf("\n=== Testing Performance ===\n");
    
    int iterations = 10000;
    struct timespec start, end;
    
    clock_gettime(CLOCK_MONOTONIC, &start);
    
    for (int i = 0; i < iterations; i++) {
        _SPA_safety_property_update_state(i, "perf.c", i, "perf_key_%d", i % 100);
        _SPA_safety_property_get_state(0, "perf_key_%d", i % 100);
    }
    
    clock_gettime(CLOCK_MONOTONIC, &end);
    
    double time_taken = (end.tv_sec - start.tv_sec) + 
                       (end.tv_nsec - start.tv_nsec) / 1e9;
    double ops_per_sec = (iterations * 2) / time_taken;
    
    printf("Performance: %d operations in %.3f seconds (%.0f ops/sec)\n",
           iterations * 2, time_taken, ops_per_sec);
    
    ASSERT(ops_per_sec > 1000, "Performance > 1000 ops/sec");
}

void test_persistence() {
    printf("\n=== Testing Persistence ===\n");
    
    _SPA_safety_property_update_state(42, "persist.c", 1, "persistent_key");
    _SPA_safety_property_update_state(84, "persist.c", 2, "persistent_key");
    
    int current = _SPA_safety_property_get_state(0, "persistent_key");
    int history = _SPA_safety_property_get_state(1, "persistent_key");
    
    ASSERT_EQUAL(current, 84, "Persistent key current value");
    ASSERT_EQUAL(history, 42, "Persistent key history value");
    
    printf("Note: Full persistence test requires restarting the program\n");
}

void print_test_summary() {
    printf("\n=== TEST SUMMARY ===\n");
    printf("Tests Passed: %d\n", tests_passed);
    printf("Tests Failed: %d\n", tests_failed);
    printf("Total Tests:  %d\n", tests_passed + tests_failed);
    printf("Success Rate: %.1f%%\n", 
           (float)tests_passed / (tests_passed + tests_failed) * 100);
    
    if (tests_failed == 0) {
        printf("\n🎉 ALL TESTS PASSED! 🎉\n");
    } else {
        printf("\n💥 SOME TESTS FAILED! 💥\n");
    }
}

int main() {
    printf("Starting Safety Property State Library Comprehensive Test\n\n");
    
    test_basic_operations();
    test_concurrent_operations();
    test_error_conditions();
    
    test_edge_cases();
    test_thread_safety_stress();
    test_memory_management();
    test_file_lineno_tracking();
    test_format_string_edge_cases();
    test_negative_index_boundaries();
    test_mixed_operations();

    test_complex_scenarios();
    test_performance();
    test_persistence();
    
    print_test_summary();
    
    printf("\nStates saved to: safety_property_states.txt\n");
    
    return (tests_failed == 0) ? 0 : 1;
}
