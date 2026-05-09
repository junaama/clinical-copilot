<?php

/**
 * Co-Pilot Lab Writer Module Bootstrap entry point.
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
use OpenEMR\Modules\CopilotLabWriter\Bootstrap;

$projectDir = OEGlobalsBag::getInstance()->getProjectDir();
$classLoader = new ModulesClassLoader($projectDir);
$classLoader->registerNamespaceIfNotExists(
    'OpenEMR\\Modules\\CopilotLabWriter\\',
    __DIR__ . DIRECTORY_SEPARATOR . 'src'
);

$eventDispatcher = OEGlobalsBag::getInstance()->getKernel()->getEventDispatcher();
$bootstrap = new Bootstrap($eventDispatcher);
$bootstrap->subscribeToEvents();
