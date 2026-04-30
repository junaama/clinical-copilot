<?php

/**
 * Audit entrypoint — receives one audit row per chat turn from the iframe.
 *
 * Authentication: relies on the OpenEMR session cookie (the iframe is
 * loaded from a parent frame served by the same OpenEMR origin via
 * embed.php). CSRF: validated via OpenEMR's CsrfUtils helper using a token
 * passed in the X-CSRF-Token header.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

require_once __DIR__ . '/../../../../globals.php';

use OpenEMR\BC\ServiceContainer;
use OpenEMR\Common\Csrf\CsrfUtils;
use OpenEMR\Modules\CopilotLauncher\Controller\AuditApiController;
use OpenEMR\Modules\CopilotLauncher\Service\AgentAuditLogger;
use OpenEMR\Modules\CopilotLauncher\Service\QueryUtilsExecutor;
use Symfony\Component\HttpFoundation\Request;

header('Content-Type: application/json');

$request = Request::createFromGlobals();

if ($request->getMethod() !== 'POST') {
    http_response_code(405);
    echo json_encode(['error' => 'method not allowed']);
    return;
}

/** @var \Symfony\Component\HttpFoundation\Session\SessionInterface $session */
$csrfToken = (string) ($request->headers->get('X-CSRF-Token') ?? '');
if ($csrfToken === '' || !CsrfUtils::verifyCsrfToken($csrfToken, $session)) {
    http_response_code(403);
    echo json_encode(['error' => 'csrf token missing or invalid']);
    return;
}

$raw = $request->getContent();
if ($raw === '') {
    http_response_code(400);
    echo json_encode(['error' => 'empty body']);
    return;
}

$decoded = null;
$jsonError = null;
try {
    /** @var mixed $decoded */
    $decoded = json_decode($raw, true, 32, JSON_THROW_ON_ERROR);
} catch (\JsonException $e) {
    $jsonError = $e->getMessage();
}
if ($jsonError !== null) {
    http_response_code(400);
    echo json_encode(['error' => 'invalid json']);
    return;
}
if (!is_array($decoded)) {
    http_response_code(400);
    echo json_encode(['error' => 'body must be a json object']);
    return;
}

$logger = ServiceContainer::getLogger();
$auditLogger = new AgentAuditLogger(new QueryUtilsExecutor(), $logger);
$controller = new AuditApiController($auditLogger, $logger);

/** @var array<string, scalar|null> $sessionView */
$sessionView = [
    'pid' => $_SESSION['pid'] ?? null,
    'authUserID' => $_SESSION['authUserID'] ?? null,
];

/** @var array<string, mixed> $body */
$body = $decoded;
$result = $controller->handle($body, $sessionView);
http_response_code($result['status']);
if ($result['status'] === 204) {
    return;
}
echo json_encode(['error' => $result['error'] ?? 'unknown']);
