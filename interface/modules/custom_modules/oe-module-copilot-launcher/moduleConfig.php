<?php

/**
 * Co-Pilot Launcher Module — manifest
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    Naama <naama.paulemont@challenger.gauntletai.com>
 * @copyright Copyright (c) 2026 Naama
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

return [
    'name' => 'Co-Pilot Launcher',
    'description' => 'Registers the Clinical Co-Pilot as a SMART app, docks it as a right-edge iframe in the patient chart, bridges citation flash events, and writes to agent_audit.',
    'version' => '1.0.0',
    'author' => 'Naama',
    'license' => 'GPL-3.0',
    'acl_category' => 'admin',
    'acl_section' => 'super',

    'require' => [
        'openemr' => '>=7.0.0',
    ],

    'tables' => [
        'agent_audit',
    ],

    'globals' => [
        [
            'name' => 'copilot_app_url',
            'type' => 'text',
            'default' => 'http://localhost:5173',
            'description' => 'Base URL of the Clinical Co-Pilot frontend (origin of the iframe).',
        ],
        [
            'name' => 'copilot_agent_backend_url',
            'type' => 'text',
            'default' => 'http://localhost:8000',
            'description' => 'Base URL of the Clinical Co-Pilot agent backend (used as the redirect host for the standalone OAuth client).',
        ],
        [
            'name' => 'copilot_oauth_client_secret',
            'type' => 'text',
            'default' => '',
            'description' => 'OAuth2 client secret for the EHR-launch client copilot-launcher (auto-generated at install time).',
        ],
        [
            'name' => 'copilot_oauth_standalone_client_secret',
            'type' => 'text',
            'default' => '',
            'description' => 'OAuth2 client secret for the standalone client copilot-standalone (auto-generated at install time).',
        ],
    ],

    'install' => [
        'sql' => 'sql/install.sql',
    ],

    'uninstall' => [
        'sql' => 'sql/uninstall.sql',
    ],
];
