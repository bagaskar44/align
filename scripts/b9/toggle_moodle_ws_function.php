<?php
declare(strict_types=1);

define('CLI_SCRIPT', true);
require '/var/www/html/config.php';

global $DB;
$mode = $argv[1] ?? '';
$function = 'mod_assign_get_submissions';
$service = $DB->get_record(
    'external_services',
    ['shortname' => 'align_b9_readonly'],
    '*',
    MUST_EXIST
);
$conditions = [
    'externalserviceid' => $service->id,
    'functionname' => $function,
];

if ($mode === 'disable') {
    if (!$DB->record_exists('external_services_functions', $conditions)) {
        throw new RuntimeException('Expected B9 Web Services function is already absent.');
    }
    $DB->delete_records('external_services_functions', $conditions);
    fwrite(STDOUT, "B9 Web Services test function disabled.\n");
} elseif ($mode === 'restore') {
    if (!$DB->record_exists('external_services_functions', $conditions)) {
        $DB->insert_record('external_services_functions', (object)$conditions);
    }
    fwrite(STDOUT, "B9 Web Services test function restored.\n");
} else {
    throw new InvalidArgumentException('Use disable or restore.');
}
