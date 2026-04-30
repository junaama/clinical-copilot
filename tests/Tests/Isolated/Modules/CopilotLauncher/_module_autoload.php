<?php

/**
 * Lightweight autoloader shim for isolated tests.
 *
 * Production wires the module via OpenEMR's module installer; isolated unit
 * tests run on the host without that machinery, so we register a tiny PSR-4
 * loader for the module's namespace pointing at its src/ directory.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

spl_autoload_register(static function (string $class): void {
    $prefix = 'OpenEMR\\Modules\\CopilotLauncher\\';
    if (!str_starts_with($class, $prefix)) {
        return;
    }
    $relative = substr($class, strlen($prefix));
    // __DIR__ = tests/Tests/Isolated/Modules/CopilotLauncher
    // dirname(__DIR__, 5) = project root.
    $path = dirname(__DIR__, 5)
        . '/interface/modules/custom_modules/oe-module-copilot-launcher/src/'
        . str_replace('\\', '/', $relative)
        . '.php';
    if (is_file($path)) {
        require_once $path;
    }
});
