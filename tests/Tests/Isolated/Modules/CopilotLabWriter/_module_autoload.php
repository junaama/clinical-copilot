<?php

/**
 * Lightweight autoloader shim for Copilot lab writer isolated tests.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

spl_autoload_register(static function (string $class): void {
    $prefix = 'OpenEMR\\Modules\\CopilotLabWriter\\';
    if (!str_starts_with($class, $prefix)) {
        return;
    }
    $relative = substr($class, strlen($prefix));
    $path = dirname(__DIR__, 5)
        . '/interface/modules/custom_modules/oe-module-copilot-lab-writer/src/'
        . str_replace('\\', '/', $relative)
        . '.php';
    if (is_file($path)) {
        require_once $path;
    }
});
