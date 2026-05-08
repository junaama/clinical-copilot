<?php

/**
 * Modern patient dashboard host route.
 *
 * Verifies the existing OpenEMR session and patient context, resolves the
 * internal pid to a FHIR-compatible patientUuid, and serves the React/Vite
 * patient dashboard with an inlined boot configuration object.
 *
 * Handles CareTeam save POST requests using the existing CareTeamService
 * and provides CareTeam edit metadata in the boot config for the inline
 * editor.
 *
 * The legacy PHP-rendered dashboard is preserved at demographics_legacy.php.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

require_once("../../globals.php");

use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Session\SessionWrapperFactory;
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Common\Utils\ValidationUtils;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Services\CareTeamService;
use OpenEMR\Services\ListService;
use OpenEMR\Services\PatientService;

$session = SessionWrapperFactory::getInstance()->getActiveSession();
$globalsBag = OEGlobalsBag::getInstance();
$webRoot = $globalsBag->getWebRoot();

// Resolve patient ID from session or query parameter.
if (!isset($pid)) {
    $pid = $session->get('pid') ?? $_GET['pid'] ?? null;
}

// Handle set_pid navigation (same as legacy route).
if (isset($_GET['set_pid'])) {
    require_once("$srcdir/pid.inc.php");
    setpid($_GET['set_pid']);
    $ptService = new PatientService();
    $newPatient = $ptService->findByPid($pid);
    $ptService->touchRecentPatientList($newPatient);
    if (isset($_GET['set_encounterid']) && ((int) $_GET['set_encounterid'] > 0)) {
        $encounter = (int) $_GET['set_encounterid'];
        \OpenEMR\Common\Session\SessionUtil::setSession('encounter', $encounter);
    }
}

if (empty($pid)) {
    die('Patient context required. Please select a patient first.');
}

// ---------------------------------------------------------------
// Handle CareTeam save POST (before rendering the page).
// Delegates to existing CareTeamService::saveCareTeam(...).
// ---------------------------------------------------------------
if ($_SERVER['REQUEST_METHOD'] === 'POST' && ($_POST['save_care_team'] ?? '') === 'true') {
    CsrfUtils::verifyCsrfToken($_POST['csrf_token_form'] ?? '', $session);

    $teamId = ValidationUtils::validateInt($_POST['team_id'] ?? '');
    $teamId = $teamId === false ? null : $teamId;
    $teamName = trim((string) ($_POST['team_name'] ?? ''));
    $team = is_array($_POST['team'] ?? null) ? $_POST['team'] : [];
    $teamStatus = trim((string) ($_POST['team_status'] ?? 'active'));

    $careTeamService = new CareTeamService();
    $careTeamService->saveCareTeam((int) $pid, $teamId, $teamName, $team, $teamStatus);

    // Redirect to avoid form resubmission on refresh (PRG pattern).
    header('Location: ' . $_SERVER['REQUEST_URI']);
    exit;
}

// Resolve pid to FHIR-compatible UUID.
$patientService = new PatientService();
$patientUuidBinary = $patientService->getUuid((string) $pid);
$patientUuid = $patientUuidBinary !== false
    ? UuidRegistry::uuidToString($patientUuidBinary)
    : '';

if (empty($patientUuid)) {
    die('Unable to resolve patient UUID for pid ' . htmlspecialchars((string) $pid, ENT_QUOTES, 'UTF-8'));
}

// Construct FHIR base URL using the standard site path.
$siteId = $session->get('site_id') ?? 'default';
$fhirBaseUrl = $webRoot . '/apis/' . urlencode((string) $siteId) . '/fhir';

// Build paths for legacy/modern navigation.
$summaryDir = $webRoot . '/interface/patient_file/summary';
$legacyDashboardUrl = $summaryDir . '/demographics_legacy.php';
$modernDashboardUrl = $summaryDir . '/demographics.php';

// CSRF token for any form submissions.
$csrfToken = CsrfUtils::collectCsrfToken(session: $session);

// ---------------------------------------------------------------
// Build CareTeam edit metadata (only if user has write ACL).
// ---------------------------------------------------------------
$careTeamEditConfig = null;

if (AclMain::aclCheckCore('patients', 'demo', '', 'write')) {
    $careTeamService = new CareTeamService();
    $listService = new ListService();

    // Existing team data
    $careTeamResult = $careTeamService->getCareTeamData((int) $pid);
    $existingMembers = [];
    foreach (($careTeamResult['members'] ?? []) as $member) {
        $existingMembers[] = [
            'memberType' => ($member['member_type'] ?? 'user') === 'contact' ? 'contact' : 'user',
            'userId' => $member['user_id'] !== null ? (int) $member['user_id'] : null,
            'contactId' => $member['contact_id'] !== null ? (int) $member['contact_id'] : null,
            'role' => $member['role'] ?? '',
            'facilityId' => $member['facility_id'] !== null ? (int) $member['facility_id'] : null,
            'providerSince' => $member['provider_since'] ?? null,
            'status' => $member['status'] ?? 'active',
            'note' => $member['note'] ?? null,
            'userName' => $member['user_name'] ?? null,
            'contactName' => $member['contact_name'] ?? null,
        ];
    }

    // Available users (providers)
    $usersResult = QueryUtils::sqlStatementThrowException(
        "SELECT u.id, u.fname, u.lname, u.physician_type
         FROM users u
         WHERE active = 1 AND username IS NOT NULL AND fname IS NOT NULL
         ORDER BY lname, fname",
        []
    );
    $users = [];
    while ($user = QueryUtils::fetchArrayFromResultSet($usersResult)) {
        $users[] = [
            'id' => (int) $user['id'],
            'name' => $user['lname'] . ', ' . $user['fname'],
            'physicianType' => $user['physician_type'] ?: null,
        ];
    }

    // Available facilities
    $facilitiesResult = QueryUtils::sqlStatementThrowException(
        "SELECT id, name FROM facility
         WHERE service_location = 1 OR billing_location = 1
         ORDER BY name",
        []
    );
    $facilities = [];
    while ($facility = QueryUtils::fetchArrayFromResultSet($facilitiesResult)) {
        $facilities[] = [
            'id' => (int) $facility['id'],
            'name' => $facility['name'],
        ];
    }

    // Available roles
    $roleOptions = $listService->getOptionsByListName('care_team_roles');
    $roles = [];
    foreach ($roleOptions as $role) {
        $roles[] = [
            'id' => $role['option_id'],
            'title' => $role['title'],
        ];
    }

    // Available statuses
    $statusOptions = $listService->getOptionsByListName('Care_Team_Status');
    $statuses = [];
    foreach ($statusOptions as $status) {
        $statuses[] = [
            'id' => $status['option_id'],
            'title' => $status['title'],
        ];
    }

    // Related persons
    $relatedPersons = [];
    // Related person integration uses ContactService which requires more setup;
    // include an empty array as placeholder — the PHP CareTeamViewCard handles
    // this through ContactRelationService but that requires patient contact
    // context that is not always available.

    $careTeamEditConfig = [
        'teamId' => isset($careTeamResult['team_id']) ? (int) $careTeamResult['team_id'] : null,
        'teamName' => $careTeamResult['team_name'] ?? 'default',
        'teamStatus' => $careTeamResult['team_status'] ?? 'active',
        'users' => $users,
        'relatedPersons' => $relatedPersons,
        'facilities' => $facilities,
        'roles' => $roles,
        'statuses' => $statuses,
        'existingMembers' => $existingMembers,
    ];
}

// Path to built React assets (relative to web root).
$assetsPath = $webRoot . '/public/assets/patient-dashboard';

// Find the built JS entry point.
$assetsDir = $globalsBag->getProjectDir() . '/public/assets/patient-dashboard/assets';
$jsFile = '';
if (is_dir($assetsDir)) {
    $files = glob($assetsDir . '/index-*.js');
    if (!empty($files)) {
        $jsFile = $assetsPath . '/assets/' . basename($files[0]);
    }
}

$bootConfig = [
    'pid' => (int) $pid,
    'patientUuid' => $patientUuid,
    'webRoot' => $webRoot,
    'fhirBaseUrl' => $fhirBaseUrl,
    'legacyDashboardUrl' => $legacyDashboardUrl,
    'modernDashboardUrl' => $modernDashboardUrl,
    'csrfToken' => $csrfToken,
];

if ($careTeamEditConfig !== null) {
    $bootConfig['careTeamEdit'] = $careTeamEditConfig;
}

?><!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Patient Dashboard</title>
    <style>
        body { margin: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
        .dashboard-error { padding: 2rem; color: #721c24; background: #f8d7da; border: 1px solid #f5c6cb; border-radius: 4px; margin: 1rem; }
        .dashboard-header { display: flex; justify-content: space-between; align-items: center; padding: 0.75rem 1.5rem; background: #f8f9fa; border-bottom: 1px solid #dee2e6; }
        .dashboard-header h1 { margin: 0; font-size: 1.25rem; }
        .legacy-link { color: #007bff; text-decoration: none; font-size: 0.875rem; }
        .legacy-link:hover { text-decoration: underline; }
    </style>
    <script>
        window.__OPENEMR_PATIENT_DASHBOARD__ = <?php echo json_encode(
            $bootConfig,
            JSON_HEX_TAG | JSON_HEX_AMP | JSON_HEX_APOS | JSON_HEX_QUOT | JSON_THROW_ON_ERROR
        ); ?>;
    </script>
</head>
<body>
    <div id="patient-dashboard-root"></div>
    <?php if (!empty($jsFile)) : ?>
        <script type="module" src="<?php echo htmlspecialchars($jsFile, ENT_QUOTES, 'UTF-8'); ?>"></script>
    <?php else : ?>
        <div class="dashboard-error" role="alert">
            <p>Patient dashboard assets not found. Run <code>npm run build</code> in <code>frontend/patient-dashboard/</code>.</p>
        </div>
    <?php endif; ?>
</body>
</html>
