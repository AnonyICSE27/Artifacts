#define SAFETY_PROPERTY_EXPORTS
#include <stdlib.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdint.h>
#include <string.h>
#include <pthread.h>

#include "safety_property_state.h"

#define MAX_KEY_LENGTH 1024
#define MAX_FILE_LENGTH 1024
#define MAX_HISTORY_PER_KEY UINT32_MAX

/**
 * Represents a single state entry with value and location information
 */
typedef struct StateEntry {
    long long value;
    char file[MAX_FILE_LENGTH];
    int lineno;
    struct StateEntry* next;
} StateEntry;

/**
 * Represents the state history for a specific key
 */
typedef struct KeyState {
    char key[MAX_KEY_LENGTH];
    StateEntry* history;  // Linked list of historical entries (most recent first)
    StateEntry* current;  // Current state entry
    struct KeyState* next;
} KeyState;

static KeyState* g_state_table = NULL;
static pthread_mutex_t g_state_mutex = PTHREAD_MUTEX_INITIALIZER;
static const char* SAVE_FILE = "/safety_property_states.txt";

/**
 * Prints debug messages when DEBUG environment variable is set to "1"
 * @param fmt Format string for debug message
 * @param ... Variable arguments for format string
 */
static void debug_printf(const char* fmt, ...) {
    const char *debug_flag = getenv("DEBUG");
    if (!debug_flag || strcmp(debug_flag, "1") != 0) {
        return;
    }

    va_list args;
    va_start(args, fmt);
    printf("[DEBUG] ");
    vprintf(fmt, args);
    printf("\n");
    va_end(args);
}

/**
 * Finds existing key state or creates a new one if not found
 * @param key The key to search for or create
 * @return Pointer to the KeyState structure, or NULL if allocation failed
 */
static KeyState* find_or_create_key_state(const char* key) {
    KeyState** prev = &g_state_table;
    KeyState* current = g_state_table;
    
    while (current != NULL) {
        if (strcmp(current->key, key) == 0) {
            return current;
        }
        prev = &current->next;
        current = current->next;
    }
    
    KeyState* new_state = malloc(sizeof(KeyState));
    if (!new_state) return NULL;
    
    strncpy(new_state->key, key, MAX_KEY_LENGTH - 1);
    new_state->key[MAX_KEY_LENGTH - 1] = '\0';
    new_state->history = NULL;
    new_state->current = NULL;
    new_state->next = NULL;
    
    *prev = new_state;
    return new_state;
}

/**
 * Adds a new entry to the history list for a key state
 * Maintains history size within MAX_HISTORY_PER_KEY limit
 * @param key_state The KeyState to add history to
 * @param value The value to store in history
 * @param file Source file where the state was recorded
 * @param lineno Line number where the state was recorded
 */
static void add_history_entry(KeyState* key_state, long long value, const char* file, int lineno) {
    StateEntry* new_entry = malloc(sizeof(StateEntry));
    if (!new_entry) return;
    
    new_entry->value = value;
    strncpy(new_entry->file, file, MAX_FILE_LENGTH - 1);
    new_entry->file[MAX_FILE_LENGTH - 1] = '\0';
    new_entry->lineno = lineno;
    new_entry->next = key_state->history;
    key_state->history = new_entry;
    
    // Trim history if it exceeds maximum allowed entries
    long long count = 0;
    StateEntry* hist = key_state->history;
    while (hist != NULL && count < MAX_HISTORY_PER_KEY) {
        count++;
        hist = hist->next;
    }
    
    if (hist != NULL) {
        StateEntry* prev = NULL;
        StateEntry* curr = key_state->history;
        while (curr->next != NULL) {
            prev = curr;
            curr = curr->next;
        }
        if (prev) {
            prev->next = NULL;
        } else {
            key_state->history = NULL;
        }
        free(curr);
    }
}

/**
 * Saves all current states and their history to file on program exit
 * Called automatically via atexit() registration
 */
static void save_states_on_exit(void) {
    pthread_mutex_lock(&g_state_mutex);
    
    const char *save_file = getenv("SAFETY_PROPERTY_SAVE_FILE");
    if (!save_file) {
        save_file = SAVE_FILE;
    }

    FILE* fp = fopen(save_file, "w");
    if (!fp) {
        debug_printf("Failed to open %s for writing", save_file);
        pthread_mutex_unlock(&g_state_mutex);
        return;
    }
    
    KeyState* key_state = g_state_table;
    while (key_state != NULL) {
        if (key_state->current) {
            fprintf(fp, "KEY: %s\n", key_state->key);
            fprintf(fp, "CURRENT: %lld %s %d\n", 
                    key_state->current->value,
                    key_state->current->file,
                    key_state->current->lineno);
            
            StateEntry* hist = key_state->history;
            while (hist != NULL) {
                fprintf(fp, "HISTORY: %lld %s %d\n", 
                        hist->value, hist->file, hist->lineno);
                hist = hist->next;
            }
            fprintf(fp, "END_KEY\n\n");
        }
        key_state = key_state->next;
    }
    
    debug_printf("Successfully saved safety property states to %s", SAVE_FILE);
    fclose(fp);
    pthread_mutex_unlock(&g_state_mutex);
}

/**
 * Library initialization function called automatically before main()
 * Registers save_states_on_exit to be called at program termination
 */
__attribute__((constructor)) static void init_library(void) {
    debug_printf("Initializing safety property state library...");
}

/**
 * Library cleanup function called automatically after main()
 * Saves all current states and their history to file on program exit
 */
__attribute__((destructor)) static void cleanup_library(void) {
    #FIXME: not work!!
    debug_printf("Saving safety property states on exit...\n");
    save_states_on_exit();
}

/**
 * Updates the safety property state for a given key
 * @param value The new value to set
 * @param file Source file where update occurs (automatically captured)
 * @param lineno Line number where update occurs (automatically captured)
 * @param key_fmt Format string for the key name
 * @param ... Variable arguments for key format string
 * @return 0 on success, -1 on error
 */
SAFETY_PROP_STATE_API int _SPA_safety_property_update_state(long long value, const char* file, int lineno, const char *key_fmt, ...) {
    char key[MAX_KEY_LENGTH];
    va_list args;
    va_start(args, key_fmt);
    vsnprintf(key, sizeof(key), key_fmt, args);
    va_end(args);
    
    if (!key[0] || !file) return -1;
    
    pthread_mutex_lock(&g_state_mutex);
    
    KeyState* key_state = find_or_create_key_state(key);
    if (!key_state) {
        pthread_mutex_unlock(&g_state_mutex);
        return -1;
    }
    
    if (key_state->current) {
        add_history_entry(key_state, key_state->current->value, key_state->current->file, key_state->current->lineno);
        key_state->current->value = value;
        strncpy(key_state->current->file, file, MAX_FILE_LENGTH - 1);
        key_state->current->file[MAX_FILE_LENGTH - 1] = '\0';
        key_state->current->lineno = lineno;
    } else {
        StateEntry* entry = malloc(sizeof(StateEntry));
        if (entry) {
            entry->value = value;
            strncpy(entry->file, file, MAX_FILE_LENGTH - 1);
            entry->file[MAX_FILE_LENGTH - 1] = '\0';
            entry->lineno = lineno;
            entry->next = NULL;
            key_state->current = entry;
        }
    }
    
    pthread_mutex_unlock(&g_state_mutex);
    debug_printf("Updated state: %s = %lld (%s:%d)", key, value, file, lineno);
    return 0;
}

/**
 * Retrieves a safety property state value by key and index
 * @param index 0 for current value, positive for history index (1=most recent),
 *              negative for reverse index (-1=oldest, -2=second oldest, etc.)
 * @param key_fmt Format string for the key name
 * @param ... Variable arguments for key format string
 * @return The state value on success, -1 if not found
 */
SAFETY_PROP_STATE_API int _SPA_safety_property_get_state(long long index, const char *key_fmt, ...) {
    char key[MAX_KEY_LENGTH];
    va_list args;
    va_start(args, key_fmt);
    vsnprintf(key, sizeof(key), key_fmt, args);
    va_end(args);
    
    if (!key[0]) return -1;
    
    pthread_mutex_lock(&g_state_mutex);
    
    KeyState* key_state = g_state_table;
    while (key_state != NULL) {
        if (strcmp(key_state->key, key) == 0) {
            break;
        }
        key_state = key_state->next;
    }
    
    if (!key_state || !key_state->current) {
        pthread_mutex_unlock(&g_state_mutex);
        return -1;
    }
    
    int result = -1;
    
    if (index == 0) {
        result = key_state->current->value;
    } else if (index > 0) {
        StateEntry* hist = key_state->history;
        long long count = 1;
        while (hist != NULL) {
            if (count == index) {
                result = hist->value;
                break;
            }
            count++;
            hist = hist->next;
        }
    } else {
        StateEntry* hist = key_state->history;
        int total = 0;
        while (hist != NULL) {
            total++;
            hist = hist->next;
        }
        
        long long target_index = total + index + 1;
        if (target_index == 0) {
            result = key_state->current->value;
        } else if (target_index >= 1 && target_index <= total) {
            hist = key_state->history;
            long long count = 1;
            while (hist != NULL) {
                if (count == target_index) {
                    result = hist->value;
                    break;
                }
                count++;
                hist = hist->next;
            }
        }
    }
    
    pthread_mutex_unlock(&g_state_mutex);
    debug_printf("Get state: %s[%lld] = %d", key, index, result);
    return result;
}