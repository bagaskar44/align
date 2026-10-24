<?php
declare(strict_types=1);

define('CLI_SCRIPT', true);
require '/var/www/html/config.php';

global $DB, $CFG, $USER;
require_once($CFG->dirroot . '/course/lib.php');
require_once($CFG->dirroot . '/course/modlib.php');
require_once($CFG->dirroot . '/user/lib.php');
require_once($CFG->dirroot . '/group/lib.php');
require_once($CFG->dirroot . '/mod/assign/locallib.php');
require_once($CFG->dirroot . '/mod/lti/lib.php');
require_once($CFG->dirroot . '/mod/lti/locallib.php');

$siteadmins = explode(',', (string)$CFG->siteadmins);
$adminid = (int)$siteadmins[0];
$USER = $DB->get_record('user', ['id' => $adminid], '*', MUST_EXIST);
$adminpassword = getenv('MOODLE_B9_ADMIN_PASSWORD');
$testpassword = getenv('MOODLE_B9_TEST_PASSWORD');
if (!is_string($adminpassword) || strlen($adminpassword) < 20) {
    throw new RuntimeException('Synthetic Moodle admin password is unavailable.');
}
if (!is_string($testpassword) || strlen($testpassword) < 20) {
    throw new RuntimeException('Synthetic Moodle test password is unavailable.');
}
update_internal_user_password($USER, $adminpassword);

function b9_course(string $shortname, string $fullname): stdClass {
    global $DB;
    $existing = $DB->get_record('course', ['shortname' => $shortname]);
    if ($existing) {
        return $existing;
    }
    $category = core_course_category::get_default();
    return create_course((object)[
        'category' => $category->id,
        'fullname' => $fullname,
        'shortname' => $shortname,
        'idnumber' => $shortname,
        'summary' => 'Data sintetis untuk acceptance B9; tidak mewakili mahasiswa nyata.',
        'summaryformat' => FORMAT_PLAIN,
        'format' => 'topics',
        'visible' => 1,
        'startdate' => time() - DAYSECS,
    ]);
}

function b9_user(string $username, string $first, string $last, string $email, string $password): stdClass {
    global $DB, $CFG;
    $existing = $DB->get_record('user', ['username' => $username, 'mnethostid' => $CFG->mnet_localhost_id]);
    if ($existing) {
        update_internal_user_password($existing, $password);
        return $existing;
    }
    $record = (object)[
        'username' => $username,
        'auth' => 'manual',
        'confirmed' => 1,
        'mnethostid' => $CFG->mnet_localhost_id,
        'firstname' => $first,
        'lastname' => $last,
        'email' => $email,
        'password' => $password,
        'lang' => 'en',
        'country' => 'ID',
        'timezone' => 'Asia/Jakarta',
    ];
    $record->id = user_create_user($record, true, false);
    return $DB->get_record('user', ['id' => $record->id], '*', MUST_EXIST);
}

function b9_enrol(stdClass $course, stdClass $user, string $role): void {
    global $DB;
    $plugin = enrol_get_plugin('manual');
    if (!$plugin) {
        throw new RuntimeException('Moodle manual enrolment is unavailable.');
    }
    $instance = $DB->get_record('enrol', ['courseid' => $course->id, 'enrol' => 'manual']);
    if (!$instance) {
        $instanceid = $plugin->add_instance($course);
        $instance = $DB->get_record('enrol', ['id' => $instanceid], '*', MUST_EXIST);
    }
    $roleid = $DB->get_field('role', 'id', ['shortname' => $role], MUST_EXIST);
    if (!$DB->record_exists('user_enrolments', ['userid' => $user->id, 'enrolid' => $instance->id])) {
        $plugin->enrol_user($instance, $user->id, (int)$roleid);
    }
}

function b9_group(stdClass $course, string $name, array $members): stdClass {
    global $DB;
    $group = $DB->get_record('groups', ['courseid' => $course->id, 'name' => $name]);
    if (!$group) {
        $groupid = groups_create_group((object)[
            'courseid' => $course->id,
            'name' => $name,
            'description' => 'Kelompok sintetis B9.',
            'descriptionformat' => FORMAT_PLAIN,
        ]);
        $group = $DB->get_record('groups', ['id' => $groupid], '*', MUST_EXIST);
    }
    foreach ($members as $member) {
        if (!groups_is_member($group->id, $member->id)) {
            groups_add_member($group->id, $member->id);
        }
    }
    return $group;
}

function b9_assignment(stdClass $course, string $name, bool $teamsubmission): stdClass {
    global $DB;
    $existing = $DB->get_record('assign', ['course' => $course->id, 'name' => $name]);
    if ($existing) {
        return $existing;
    }
    $module = $DB->get_record('modules', ['name' => 'assign'], '*', MUST_EXIST);
    $info = (object)[
        'modulename' => 'assign',
        'module' => $module->id,
        'name' => $name,
        'intro' => 'Attempt final sintetis untuk pengujian integrasi ALIGN B9.',
        'introformat' => FORMAT_PLAIN,
        'alwaysshowdescription' => 1,
        'submissiondrafts' => 0,
        'requiresubmissionstatement' => 0,
        'sendnotifications' => 0,
        'sendlatenotifications' => 0,
        'sendstudentnotifications' => 0,
        'duedate' => time() + DAYSECS,
        'allowsubmissionsfromdate' => 0,
        'cutoffdate' => 0,
        'gradingduedate' => 0,
        'grade' => 100,
        'completionsubmit' => 0,
        'teamsubmission' => $teamsubmission ? 1 : 0,
        'requireallteammemberssubmit' => 0,
        'teamsubmissiongroupingid' => 0,
        'blindmarking' => 0,
        'markingworkflow' => 0,
        'markingallocation' => 0,
        'attemptreopenmethod' => 'none',
        'maxattempts' => 1,
        'assignsubmission_file_enabled' => 1,
        'assignsubmission_file_maxfiles' => 5,
        'assignsubmission_file_maxsizebytes' => 20971520,
        'assignsubmission_file_filetypes' => '.pdf,.docx,.xlsx,.jpg,.jpeg,.png',
        'assignsubmission_onlinetext_enabled' => 0,
        'assignfeedback_comments_enabled' => 1,
        'visible' => 1,
        'visibleoncoursepage' => 1,
        'section' => 0,
        'groupmode' => NOGROUPS,
        'groupingid' => 0,
        'completion' => COMPLETION_DISABLED,
        'completionexpected' => 0,
    ];
    $created = add_moduleinfo($info, $course);
    return $DB->get_record('assign', ['id' => $created->instance], '*', MUST_EXIST);
}

function b9_submission(stdClass $assignmentrecord, stdClass $course, int $userid, int $groupid, string $filename): void {
    global $DB;
    $cm = get_coursemodule_from_instance('assign', $assignmentrecord->id, $course->id, false, MUST_EXIST);
    $assignment = new assign(context_module::instance($cm->id), $cm, $course);
    $submission = $assignmentrecord->teamsubmission
        ? $assignment->get_group_submission($userid, $groupid, true)
        : $assignment->get_user_submission($userid, true);
    if (!$submission) {
        throw new RuntimeException('Moodle submission fixture could not be created.');
    }
    if ($submission->status !== ASSIGN_SUBMISSION_STATUS_SUBMITTED) {
        $submission->status = ASSIGN_SUBMISSION_STATUS_SUBMITTED;
        $submission->timecreated = time() - 600;
        $submission->timemodified = time() - 300;
        $submission->latest = 1;
        $DB->update_record('assign_submission', $submission);
    }
    $fs = get_file_storage();
    $context = context_module::instance($cm->id);
    $fixture = getenv('MOODLE_B9_SYNTHETIC_FILE') ?: '/usr/local/share/moodle-b9/final.pdf';
    if (!in_array($fixture, [
        '/usr/local/share/moodle-b9/final.pdf',
        '/usr/local/share/moodle-b9/final-v2.pdf',
    ], true) || !is_file($fixture)) {
        throw new RuntimeException('Synthetic Moodle PDF fixture is missing.');
    }
    $area = $fs->get_area_files($context->id, 'assignsubmission_file', 'submission_files', $submission->id, 'id', false);
    $expectedhash = sha1_file($fixture);
    $needsreplacement = !$area;
    foreach ($area as $storedfile) {
        if ($storedfile->get_filename() !== '.' && !hash_equals($expectedhash, $storedfile->get_contenthash())) {
            $needsreplacement = true;
            break;
        }
    }
    if ($needsreplacement) {
        $fs->delete_area_files($context->id, 'assignsubmission_file', 'submission_files', $submission->id);
        $fs->create_file_from_pathname([
            'contextid' => $context->id,
            'component' => 'assignsubmission_file',
            'filearea' => 'submission_files',
            'itemid' => $submission->id,
            'filepath' => '/',
            'filename' => $filename,
            'userid' => $userid,
        ], $fixture);
        $pluginrecord = $DB->get_record('assignsubmission_file', ['submission' => $submission->id]);
        if ($pluginrecord) {
            $pluginrecord->numfiles = 1;
            $DB->update_record('assignsubmission_file', $pluginrecord);
        } else {
            $DB->insert_record('assignsubmission_file', (object)[
                'assignment' => $assignmentrecord->id,
                'submission' => $submission->id,
                'numfiles' => 1,
            ]);
        }
        $submission->timemodified = time();
        $DB->update_record('assign_submission', $submission);
    }
}

function b9_lti_activity(stdClass $course, string $name = 'ALIGN B9 LTI Launch Activity'): bool {
    global $DB;
    $toolname = 'ALIGN B9 LTI 1.3 Tool';
    $tool = $DB->get_record('lti_types', [
        'course' => SITEID,
        'name' => $toolname,
        'ltiversion' => LTI_VERSION_1P3,
        'state' => 1,
    ]);
    $launchurl = (string)getenv('ALIGN_B9_LAUNCH_URL');
    if (!$tool || !hash_equals($launchurl, (string)$tool->baseurl)) {
        // Moodle 4.3+ requires course activities to use a preconfigured tool.
        // Keep an older placeholder hidden until the site admin registers ALIGN.
        $placeholder = $DB->get_record('lti', ['course' => $course->id, 'name' => $name]);
        if ($placeholder) {
            $cm = get_coursemodule_from_instance('lti', $placeholder->id, $course->id, false, MUST_EXIST);
            set_coursemodule_visible($cm->id, 0, 0);
        }
        return false;
    }

    $existing = $DB->get_record('lti', ['course' => $course->id, 'name' => $name]);
    if ($existing) {
        $cm = get_coursemodule_from_instance('lti', $existing->id, $course->id, false, MUST_EXIST);
        if (
            (int)$existing->typeid !== (int)$tool->id
            || (string)$existing->toolurl !== $launchurl
            || (int)$existing->launchcontainer !== LTI_LAUNCH_CONTAINER_REPLACE_MOODLE_WINDOW
        ) {
            global $CFG;
            require_once($CFG->dirroot . '/mod/lti/lib.php');
            $existing->instance = $existing->id;
            $existing->coursemodule = $cm->id;
            $existing->course = $course->id;
            $existing->typeid = $tool->id;
            $existing->toolurl = $launchurl;
            $existing->securetoolurl = $launchurl;
            // The ALIGN session cookie keeps the same SameSite=Lax policy as local login.
            // Launch in Moodle's top-level window so the browser accepts that cookie.
            $existing->launchcontainer = LTI_LAUNCH_CONTAINER_REPLACE_MOODLE_WINDOW;
            $existing->instructorchoiceacceptgrades = LTI_SETTING_NEVER;
            $existing->grade = 0;
            if (!lti_update_instance($existing, null)) {
                throw new RuntimeException('Could not attach the seeded LTI activity to the registered tool.');
            }
        }
        set_coursemodule_visible($cm->id, 1, 1);
        return true;
    }

    $module = $DB->get_record('modules', ['name' => 'lti'], '*', MUST_EXIST);
    $info = (object)[
        'modulename' => 'lti',
        'module' => $module->id,
        'name' => $name,
        'intro' => 'Aktivitas LTI khusus untuk menguji login ALIGN B9. Bukan tugas Moodle.',
        'introformat' => FORMAT_PLAIN,
        'toolurl' => (string)getenv('ALIGN_B9_LAUNCH_URL'),
        'securetoolurl' => (string)getenv('ALIGN_B9_LAUNCH_URL'),
        'typeid' => (int)$tool->id,
        'instructorchoicesendname' => LTI_SETTING_NEVER,
        'instructorchoicesendemailaddr' => LTI_SETTING_NEVER,
        'instructorchoiceacceptgrades' => LTI_SETTING_NEVER,
        'instructorchoiceallowroster' => LTI_SETTING_NEVER,
        'launchcontainer' => LTI_LAUNCH_CONTAINER_REPLACE_MOODLE_WINDOW,
        'grade' => 0,
        'visible' => 1,
        'visibleoncoursepage' => 1,
        'section' => 0,
        'groupmode' => NOGROUPS,
        'groupingid' => 0,
        'completion' => COMPLETION_DISABLED,
        'completionexpected' => 0,
    ];
    add_moduleinfo($info, $course);
    return true;
}

function b9_readonly_webservice(stdClass $serviceuser, array $courses): void {
    global $DB, $CFG;
    set_config('enablewebservices', 1);
    set_config('webserviceprotocols', 'rest');
    $shortname = 'align_b9_readonly';
    $service = $DB->get_record('external_services', ['shortname' => $shortname]);
    if (!$service) {
        $serviceid = $DB->insert_record('external_services', (object)[
            'name' => 'ALIGN B9 Read Only',
            'enabled' => 1,
            'requiredcapability' => null,
            'restrictedusers' => 1,
            'component' => '',
            'timecreated' => time(),
            'shortname' => $shortname,
            'downloadfiles' => 1,
            'uploadfiles' => 0,
        ]);
        $service = $DB->get_record('external_services', ['id' => $serviceid], '*', MUST_EXIST);
    }
    $functions = [
        'core_webservice_get_site_info',
        'mod_assign_get_assignments',
        'mod_assign_get_submissions',
        'core_enrol_get_enrolled_users',
        'core_group_get_course_groups',
        'core_group_get_group_members',
    ];
    foreach ($functions as $name) {
        if (!$DB->record_exists('external_services_functions', ['externalserviceid' => $service->id, 'functionname' => $name])) {
            $DB->insert_record('external_services_functions', (object)[
                'externalserviceid' => $service->id,
                'functionname' => $name,
            ]);
        }
    }
    $service->enabled = 1;
    $service->restrictedusers = 1;
    $service->downloadfiles = 1;
    $service->uploadfiles = 0;
    $DB->update_record('external_services', $service);
    if (!$DB->record_exists('external_services_users', [
            'externalserviceid' => $service->id,
            'userid' => $serviceuser->id,
        ])) {
        $DB->insert_record('external_services_users', (object)[
            'externalserviceid' => $service->id,
            'userid' => $serviceuser->id,
            'timecreated' => time(),
        ]);
    }
    require_once($CFG->dirroot . '/admin/roles/lib.php');
    $webserviceroleid = $DB->get_field('role', 'id', ['shortname' => 'alignb9ws']);
    if (!$webserviceroleid) {
        $webserviceroleid = create_role(
            'ALIGN B9 Read-only Web Service',
            'alignb9ws',
            'Allows the synthetic ALIGN B9 service account to use its restricted read-only Web Service.',
            ''
        );
    }
    $systemcontext = context_system::instance();
    assign_capability('webservice/rest:use', CAP_ALLOW, (int)$webserviceroleid, $systemcontext->id, true);
    if (!$DB->record_exists('role_assignments', [
            'roleid' => $webserviceroleid,
            'contextid' => $systemcontext->id,
            'userid' => $serviceuser->id,
        ])) {
        role_assign((int)$webserviceroleid, (int)$serviceuser->id, $systemcontext->id);
    }
    foreach ($courses as $course) {
        b9_enrol($course, $serviceuser, 'editingteacher');
    }

    $tokenpath = '/run/b9-secrets/ws-token.txt';
    $current = is_file($tokenpath) ? trim((string)file_get_contents($tokenpath)) : '';
    if ($current === '') {
        require_once($CFG->libdir . '/externallib.php');
        $token = \core_external\util::generate_token(
            EXTERNAL_TOKEN_PERMANENT,
            $service,
            (int)$serviceuser->id,
            context_system::instance(),
            0,
            '',
            'ALIGN B9 read-only adapter'
        );
        if (file_put_contents($tokenpath, $token . "\n", LOCK_EX) === false) {
            throw new RuntimeException('Read-only Moodle service token could not be stored.');
        }
    }
    chmod($tokenpath, 0440);
    chown($tokenpath, 10001);
    chgrp($tokenpath, 10001);
}

$course1 = b9_course('B9-IF101', 'B9 Sandbox: Pengantar Informatika');
$course2 = b9_course('B9-IF202', 'B9 Sandbox: Sistem Informasi');
$teacher1 = b9_user('b9teacher1', 'Dosen', 'B9 Satu', 'teacher1@b9.localhost', $testpassword);
$teacher2 = b9_user('b9teacher2', 'Dosen', 'B9 Dua', 'teacher2@b9.localhost', $testpassword);
$student1 = b9_user('b9student1', 'Mahasiswa', 'B9 Satu', 'student1@b9.localhost', $testpassword);
$student2 = b9_user('b9student2', 'Mahasiswa', 'B9 Dua', 'student2@b9.localhost', $testpassword);
$student3 = b9_user('b9student3', 'Mahasiswa', 'B9 Tiga', 'student3@b9.localhost', $testpassword);
$unbound = b9_user('b9unbound', 'Mahasiswa', 'Tanpa Binding B9', 'unbound@b9.localhost', $testpassword);
$roleMismatch = b9_user('b9rolemismatch', 'Mahasiswa', 'Role Mismatch B9', 'role-mismatch@b9.localhost', $testpassword);
$serviceuser = b9_user('alignb9webservice', 'ALIGN B9', 'Read Only', 'lms-service@b9.localhost', $testpassword);
foreach ([[$course1, $teacher1, 'editingteacher'], [$course1, $student1, 'student'], [$course1, $student2, 'student'], [$course1, $student3, 'student'], [$course1, $unbound, 'student'], [$course1, $roleMismatch, 'student'], [$course1, $serviceuser, 'editingteacher'], [$course2, $teacher2, 'editingteacher'], [$course2, $student3, 'student'], [$course2, $serviceuser, 'editingteacher']] as [$course, $user, $role]) {
    b9_enrol($course, $user, $role);
}
$group1 = b9_group($course1, 'IF101 Kelompok A', [$student1, $student2]);
$group2 = b9_group($course1, 'IF101 Kelompok B', [$student3]);
$individual1 = b9_assignment($course1, 'B9 Tugas Individu Final', false);
$team1 = b9_assignment($course1, 'B9 Tugas Kelompok Final', true);
$individual2 = b9_assignment($course2, 'B9 Tugas Course Kedua', false);
b9_submission($individual1, $course1, (int)$student1->id, 0, 'if101-individu-final.pdf');
b9_submission($team1, $course1, (int)$student1->id, (int)$group1->id, 'if101-kelompok-a-final.pdf');
b9_submission($individual2, $course2, (int)$student3->id, 0, 'if202-individu-final.pdf');
$course1LtiSeeded = b9_lti_activity($course1);
$course1GroupLtiSeeded = b9_lti_activity($course1, 'ALIGN B9 LTI Group Launch Activity');
$course1UnmappedLtiSeeded = b9_lti_activity($course1, 'ALIGN B9 LTI Unmapped Activity');
$course2LtiSeeded = b9_lti_activity($course2);
$ltiActivitiesSeeded = $course1LtiSeeded && $course1GroupLtiSeeded && $course1UnmappedLtiSeeded && $course2LtiSeeded;
b9_readonly_webservice($serviceuser, [$course1, $course2]);

if ($ltiActivitiesSeeded) {
    echo "Moodle B9 synthetic fixture ensured: 2 courses, synthetic launch identities, 2 groups, file assignments, mapped and unmapped LTI activities, and read-only Web Services.\n";
} else {
    echo "Moodle B9 synthetic fixture ensured; LTI activities remain unavailable until the site admin registers the ALIGN B9 LTI 1.3 Tool and reruns this seed.\n";
}
