<?php
declare(strict_types=1);

define('CLI_SCRIPT', true);
require '/var/www/html/config.php';
require_once($CFG->dirroot . '/mod/lti/locallib.php');

global $DB, $CFG, $USER;

function b9_require_https_url(string $url, string $label): array {
    $parts = parse_url($url);
    if (
        !is_array($parts)
        || ($parts['scheme'] ?? '') !== 'https'
        || empty($parts['host'])
        || isset($parts['user'])
        || isset($parts['pass'])
        || isset($parts['query'])
        || isset($parts['fragment'])
    ) {
        throw new RuntimeException($label . ' must be a trusted HTTPS URL.');
    }
    return $parts;
}

$expectedroot = (string)getenv('MOODLE_B9_WWWROOT');
$launchurl = (string)getenv('ALIGN_B9_LAUNCH_URL');
$loginurl = (string)getenv('ALIGN_B9_LOGIN_URL');
$jwksurl = (string)getenv('ALIGN_B9_JWKS_URL');
if ($expectedroot === '' || $CFG->wwwroot !== $expectedroot) {
    throw new RuntimeException('Moodle runtime URL does not match the pinned sandbox origin.');
}
$moodleparts = b9_require_https_url($CFG->wwwroot, 'Moodle origin');
$launchparts = b9_require_https_url($launchurl, 'ALIGN launch URL');
$loginparts = b9_require_https_url($loginurl, 'ALIGN login URL');
$jwks_parts = b9_require_https_url($jwksurl, 'ALIGN JWKS URL');
$launchorigin = 'https://' . $launchparts['host'] . (isset($launchparts['port']) ? ':' . $launchparts['port'] : '');
foreach ([$loginparts, $jwks_parts] as $parts) {
    $origin = 'https://' . $parts['host'] . (isset($parts['port']) ? ':' . $parts['port'] : '');
    if ($origin !== $launchorigin) {
        throw new RuntimeException('ALIGN LTI endpoints must share one HTTPS origin.');
    }
}

$name = 'ALIGN B9 LTI 1.3 Tool';
$matches = $DB->get_records('lti_types', ['course' => SITEID, 'name' => $name]);
if (count($matches) > 1) {
    throw new RuntimeException('Multiple Moodle tools use the reserved ALIGN B9 registration name.');
}
$type = $matches ? reset($matches) : new stdClass();
$isnew = empty($type->id);
if ($isnew) {
    $type->clientid = bin2hex(random_bytes(16));
}
$type->state = LTI_TOOL_STATE_CONFIGURED;
$type->name = $name;
$type->description = 'Local synthetic ALIGN B9 acceptance tool; grades and personal profile fields are disabled.';
$type->baseurl = $launchurl;
$type->ltiversion = LTI_VERSION_1P3;
$type->course = SITEID;

$config = new stdClass();
$config->lti_toolurl = $launchurl;
$config->lti_tooldomain = $launchparts['host'];
$config->lti_typename = $name;
$config->lti_description = $type->description;
$config->lti_ltiversion = LTI_VERSION_1P3;
$config->lti_clientid = $type->clientid;
$config->lti_coursevisible = LTI_COURSEVISIBLE_PRECONFIGURED;
$config->lti_keytype = LTI_JWK_KEYSET;
$config->lti_publickeyset = $jwksurl;
$config->lti_initiatelogin = $loginurl;
$config->lti_redirectionuris = $launchurl;
$config->lti_forcessl = 1;
$config->lti_launchcontainer = LTI_LAUNCH_CONTAINER_REPLACE_MOODLE_WINDOW;
$config->lti_sendname = LTI_SETTING_NEVER;
$config->lti_sendemailaddr = LTI_SETTING_NEVER;
$config->lti_acceptgrades = LTI_SETTING_NEVER;
$config->ltiservice_gradesynchronization = 0;
$config->ltiservice_memberships = 0;
$config->ltiservice_toolsettings = 0;
$config->lti_contentitem = 0;

$siteadmins = explode(',', (string)$CFG->siteadmins);
$USER = $DB->get_record('user', ['id' => (int)$siteadmins[0]], '*', MUST_EXIST);
if ($isnew) {
    $typeid = lti_add_type($type, $config);
} else {
    lti_update_type($type, $config);
    $typeid = (int)$type->id;
}
$saved = lti_get_type($typeid);
$platform = get_tool_type_config($saved);
if (
    $saved->state != LTI_TOOL_STATE_CONFIGURED
    || $saved->ltiversion !== LTI_VERSION_1P3
    || $saved->baseurl !== $launchurl
    || $platform['platformid'] !== $CFG->wwwroot
) {
    throw new RuntimeException('Moodle did not persist the expected LTI 1.3 registration.');
}

$issuerparts = b9_require_https_url($platform['platformid'], 'Moodle issuer');
$moodleorigin = 'https://' . $moodleparts['host'] . (isset($moodleparts['port']) ? ':' . $moodleparts['port'] : '');
$issuerorigin = 'https://' . $issuerparts['host'] . (isset($issuerparts['port']) ? ':' . $issuerparts['port'] : '');
if ($moodleorigin !== $issuerorigin) {
    throw new RuntimeException('Moodle issuer and configured origin do not match.');
}

echo json_encode([
    'issuer' => $platform['platformid'],
    'client_id' => $platform['clientid'],
    'deployment_id' => (string)$platform['deploymentid'],
    'authorization_url' => $platform['authrequesturl'],
    'token_url' => $platform['accesstokenurl'],
    'jwks_url' => $platform['publickeyseturl'],
    'moodle_origin' => $moodleorigin,
    'tool_id' => (int)$typeid,
], JSON_THROW_ON_ERROR | JSON_UNESCAPED_SLASHES) . PHP_EOL;
