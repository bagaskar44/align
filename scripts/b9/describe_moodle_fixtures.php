<?php
declare(strict_types=1);

define('CLI_SCRIPT', true);
require '/var/www/html/config.php';

global $CFG, $DB;

$courseSpecs = [
    'B9-IF101' => [
        'activities' => [
            'individual' => 'ALIGN B9 LTI Launch Activity',
            'group' => 'ALIGN B9 LTI Group Launch Activity',
            'unmapped' => 'ALIGN B9 LTI Unmapped Activity',
        ],
        'assignments' => [
            'individual' => 'B9 Tugas Individu Final',
            'group' => 'B9 Tugas Kelompok Final',
        ],
    ],
    'B9-IF202' => [
        'activities' => ['individual' => 'ALIGN B9 LTI Launch Activity'],
        'assignments' => ['individual' => 'B9 Tugas Course Kedua'],
    ],
];
$fixture = [
    'moodle_release' => $CFG->release,
    'moodle_version' => (string)$CFG->version,
    'users' => [],
    'groups' => [],
    'courses' => [],
];
foreach (['b9teacher1', 'b9teacher2', 'b9student1', 'b9student2', 'b9student3', 'b9unbound', 'b9rolemismatch'] as $username) {
    $user = $DB->get_record('user', ['username' => $username, 'mnethostid' => $CFG->mnet_localhost_id], '*', MUST_EXIST);
    $fixture['users'][$username] = ['id' => (int)$user->id];
}
foreach ($courseSpecs as $shortname => $spec) {
    $course = $DB->get_record('course', ['shortname' => $shortname], '*', MUST_EXIST);
    $activities = [];
    foreach ($spec['activities'] as $key => $name) {
        $activity = $DB->get_record('lti', ['course' => $course->id, 'name' => $name], '*', MUST_EXIST);
        $activities[$key] = ['resource_link_id' => (string)$activity->id];
    }
    $assignments = [];
    foreach ($spec['assignments'] as $key => $name) {
        $assignment = $DB->get_record('assign', ['course' => $course->id, 'name' => $name], '*', MUST_EXIST);
        $assignments[$key] = ['id' => (int)$assignment->id];
    }
    $fixture['courses'][$shortname] = [
        'id' => (int)$course->id,
        'context_id' => (string)$course->id,
        'activities' => $activities,
        'assignments' => $assignments,
    ];
}
foreach (['IF101 Kelompok A', 'IF101 Kelompok B'] as $name) {
    $course = $DB->get_record('course', ['shortname' => 'B9-IF101'], '*', MUST_EXIST);
    $group = $DB->get_record('groups', ['courseid' => $course->id, 'name' => $name], '*', MUST_EXIST);
    $fixture['groups'][$name] = ['id' => (int)$group->id];
}

echo json_encode($fixture, JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) . PHP_EOL;
