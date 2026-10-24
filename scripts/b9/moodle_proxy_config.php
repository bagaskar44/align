<?php
declare(strict_types=1);

$config = '/var/www/html/config.php';
$contents = file_get_contents($config);
if ($contents === false) {
    throw new RuntimeException('Moodle configuration could not be read.');
}
$needle = "require_once(__DIR__ . '/lib/setup.php');";
$settings = "\n\$CFG->reverseproxy = true;\n\$CFG->sslproxy = true;\n\$CFG->cookiesecure = true;\n";
if (str_contains($contents, '$CFG->reverseproxy = true;')) {
    exit(0);
}
if (!str_contains($contents, $needle)) {
    throw new RuntimeException('Moodle configuration bootstrap point was not found.');
}
$contents = str_replace($needle, $settings . $needle, $contents);
if (file_put_contents($config, $contents, LOCK_EX) === false) {
    throw new RuntimeException('Moodle reverse-proxy settings could not be saved.');
}
