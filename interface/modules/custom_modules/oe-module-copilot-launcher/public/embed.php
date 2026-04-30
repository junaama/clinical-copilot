<?php

/**
 * Co-Pilot embed entrypoint.
 *
 * Loads OpenEMR's globals.php (session, auth, ACLs), parses pid from the
 * query string into a PatientPid via EmbedController, then defers to
 * SmartLaunchController to mint a SMART launch token. The Twig template
 * renders the iframe with the SMART launch URL.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

require_once __DIR__ . '/../../../../globals.php';

use OpenEMR\BC\ServiceContainer;
use OpenEMR\Common\Acl\AclMain;
use OpenEMR\Common\Twig\TwigContainer;
use OpenEMR\Common\Uuid\UuidRegistry;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\FHIR\Config\ServerConfig;
use OpenEMR\FHIR\SMART\SMARTLaunchToken;
use OpenEMR\Modules\CopilotLauncher\Controller\EmbedController;
use OpenEMR\Services\PatientService;
use Symfony\Component\HttpFoundation\Request;

if (!AclMain::aclCheckCore('patients', 'demo')) {
    http_response_code(403);
    echo 'Access denied';
    return;
}

$logger = ServiceContainer::getLogger();
$globals = OEGlobalsBag::getInstance();
$copilotAppUrl = $globals->getString('copilot_app_url');
if ($copilotAppUrl === '') {
    $copilotAppUrl = 'http://localhost:5173';
}
$issuer = (new ServerConfig())->getFhirUrl();

$controller = new EmbedController(
    logger: $logger,
    copilotAppUrl: $copilotAppUrl,
    issuer: $issuer,
);

/** @var array<string, scalar|null> $sessionView */
$sessionView = [
    'pid' => $_SESSION['pid'] ?? null,
    'authUserID' => $_SESSION['authUserID'] ?? null,
    'authUser' => $_SESSION['authUser'] ?? null,
];

$request = Request::createFromGlobals();
/** @var array<string, scalar|null> $query */
$query = [];
foreach ($request->query->all() as $key => $value) {
    if (is_scalar($value) || $value === null) {
        $query[$key] = $value;
    }
}

$result = $controller->render($query, $sessionView);

if (!$result->isOk()) {
    http_response_code($result->statusCode);
    echo htmlspecialchars((string) $result->errorMessage, ENT_QUOTES);
    return;
}

assert($result->pid !== null);

$puuid = null;
$launchCode = null;
$launchError = null;
try {
    UuidRegistry::createMissingUuidsForTables(['patient_data']);
    $patientService = new PatientService();
    $uuidBytes = $patientService->getUuid((string) $result->pid->value);
    if ($uuidBytes === false) {
        $launchError = 'patient uuid not found';
    } else {
        $puuid = UuidRegistry::uuidToString($uuidBytes);
        $launchToken = new SMARTLaunchToken($puuid, null);
        $launchToken->setIntent(SMARTLaunchToken::INTENT_PATIENT_DEMOGRAPHICS_DIALOG);
        $launchCode = $launchToken->serialize();
    }
} catch (\RuntimeException | \LogicException $e) {
    $logger->error('Co-Pilot launch token generation failed', ['exception' => $e]);
    $launchError = 'failed to initialize co-pilot launch';
}
if ($launchError !== null || $puuid === null || $launchCode === null) {
    http_response_code(500);
    echo 'Failed to initialize Co-Pilot launch';
    return;
}

$twig = (new TwigContainer(__DIR__ . '/../templates', $globals->getKernel()))->getTwig();
echo $twig->render('iframe-host.html.twig', [
    'copilot_app_url' => $copilotAppUrl,
    'launch_code' => $launchCode,
    'issuer' => $issuer,
    'patient_uuid' => $puuid,
]);
