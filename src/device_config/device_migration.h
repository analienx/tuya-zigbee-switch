#ifndef DEVICE_CONFIG_DEVICE_MIGRATION_H_
#define DEVICE_CONFIG_DEVICE_MIGRATION_H_

// Runs build-time-configured, device-specific one-shot migrations. Must be
// called before parse_config() so both the stored device config and the
// relay-cluster NVM the migration touches are still in their raw form.
//
// The migration is gated by an exact stored-config predicate and a persisted
// marker, never by DEFAULT_CONFIG: a valid stored config always outranks the
// compiled-in default.
void handle_device_specific_migrations(void);

#endif /* DEVICE_CONFIG_DEVICE_MIGRATION_H_ */
