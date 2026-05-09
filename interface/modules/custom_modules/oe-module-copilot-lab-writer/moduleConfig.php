<?php

/**
 * Co-Pilot Lab Writer Module manifest.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Naama <naama.paulemont@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Naama
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

return [
    'name' => 'Co-Pilot Lab Writer',
    'description' => 'Adds a SMART-protected API endpoint that persists Co-Pilot extracted lab results into native OpenEMR procedure tables.',
    'version' => '1.0.0',
    'author' => 'Naama',
    'license' => 'GPL-3.0',
    'acl_category' => 'patients',
    'acl_section' => 'lab',

    'require' => [
        'openemr' => '>=7.0.0',
    ],

    'tables' => [
        'copilot_lab_result_map',
    ],

    'install' => [
        'sql' => 'sql/install.sql',
    ],

    'uninstall' => [
        'sql' => 'sql/uninstall.sql',
    ],
];
