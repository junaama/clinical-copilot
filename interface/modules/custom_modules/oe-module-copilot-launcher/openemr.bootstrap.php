<?php

/**
 * Co-Pilot Launcher Module Bootstrap entry point
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Naama <naama.paulemont@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Naama
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

use OpenEMR\Core\ModulesClassLoader;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Modules\CopilotLauncher\Bootstrap;

$projectDir = OEGlobalsBag::getInstance()->getProjectDir();
$classLoader = new ModulesClassLoader($projectDir);
$classLoader->registerNamespaceIfNotExists(
    'OpenEMR\\Modules\\CopilotLauncher\\',
    __DIR__ . DIRECTORY_SEPARATOR . 'src'
);

$labWriterSrc = dirname(__DIR__) . DIRECTORY_SEPARATOR
    . 'oe-module-copilot-lab-writer' . DIRECTORY_SEPARATOR
    . 'src';
if (is_dir($labWriterSrc)) {
    $classLoader->registerNamespaceIfNotExists(
        'OpenEMR\\Modules\\CopilotLabWriter\\',
        $labWriterSrc
    );
}

$eventDispatcher = OEGlobalsBag::getInstance()->getKernel()->getEventDispatcher();
$bootstrap = new Bootstrap($eventDispatcher);
$bootstrap->subscribeToEvents();
