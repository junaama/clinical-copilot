<?php

/**
 * Co-Pilot Launcher — Bootstrap
 *
 * Wires event subscribers. Per OpenEMR module convention, all the actual work
 * is delegated to dedicated Listener / Controller / Service classes.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher;

use OpenEMR\BC\ServiceContainer;
use OpenEMR\Common\Auth\AuthHash;
use OpenEMR\Common\Database\QueryUtils;
use OpenEMR\Common\Http\HttpRestRequest;
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Events\PatientDemographics\RenderEvent;
use OpenEMR\Events\RestApiExtend\RestApiCreateEvent;
use OpenEMR\Modules\CopilotLabWriter\Controller\LabResultApiController;
use OpenEMR\Modules\CopilotLabWriter\Service\LabResultWriter;
use OpenEMR\Modules\CopilotLabWriter\Service\QueryUtilsExecutor as LabWriterQueryUtilsExecutor;
use OpenEMR\Modules\CopilotLauncher\Listeners\ChartSidebarListener;
use OpenEMR\Modules\CopilotLauncher\Service\CopilotClientRegistration;
use OpenEMR\Modules\CopilotLauncher\Service\DemoUserSeeder;
use OpenEMR\Modules\CopilotLauncher\Service\QueryUtilsExecutor;
use OpenEMR\RestControllers\Config\RestConfig;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;

final class Bootstrap
{
    public const MODULE_INSTALLATION_PATH = '/interface/modules/custom_modules/oe-module-copilot-launcher';
    public const MODULE_NAME = 'oe-module-copilot-launcher';
    private const LAB_RESULT_MAP_SQL = <<<'SQL'
CREATE TABLE IF NOT EXISTS `copilot_lab_result_map` (
  `id` bigint(20) NOT NULL AUTO_INCREMENT,
  `patient_id` bigint(20) NOT NULL,
  `source_document_reference` varchar(128) NOT NULL,
  `field_path` varchar(255) NOT NULL,
  `procedure_order_id` bigint(20) NOT NULL,
  `procedure_report_id` bigint(20) NOT NULL,
  `procedure_result_id` bigint(20) NOT NULL,
  `created_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `updated_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (`id`),
  UNIQUE KEY `copilot_lab_result_natural_key` (`patient_id`, `source_document_reference`, `field_path`),
  KEY `procedure_result_id` (`procedure_result_id`)
) ENGINE=InnoDB
SQL;

    public function __construct(
        private readonly EventDispatcherInterface $eventDispatcher,
    ) {
    }

    public function subscribeToEvents(): void
    {
        $logger = ServiceContainer::getLogger();

        $copilotAppUrl = OEGlobalsBag::getInstance()->getString('copilot_app_url');
        if ($copilotAppUrl === '') {
            $copilotAppUrl = 'http://localhost:5173';
        }

        $agentBackendUrl = OEGlobalsBag::getInstance()->getString('copilot_agent_backend_url');
        if ($agentBackendUrl === '') {
            $agentBackendUrl = 'http://localhost:8000';
        }

        // Pull session identifiers at subscribe-time. They're stable for the
        // page lifecycle, and reading them here keeps the listener pure.
        $sessionUserId = isset($_SESSION['authUserID']) && is_numeric($_SESSION['authUserID'])
            ? (int) $_SESSION['authUserID']
            : 0;
        $sessionUserName = isset($_SESSION['authUser']) && is_string($_SESSION['authUser'])
            ? $_SESSION['authUser']
            : '';

        $sidebar = new ChartSidebarListener(
            copilotAppUrl: $copilotAppUrl,
            sessionUserId: $sessionUserId,
            sessionUserName: $sessionUserName,
        );

        // Render once at the bottom of the demographics page so the floating
        // launch button + sidebar slot live outside the section list and stay
        // visible regardless of how the chart is scrolled.
        $this->eventDispatcher->addListener(
            RenderEvent::EVENT_RENDER_POST_PAGELOAD,
            $sidebar->onPatientPagePostLoad(...)
        );

        $this->eventDispatcher->addListener(
            RestApiCreateEvent::EVENT_HANDLE,
            $this->onRestApiCreate(...)
        );

        // Best-effort: ensure both SMART clients (EHR-launch + standalone) have
        // generated secrets. Skipped entirely when both secrets are already
        // present, to avoid hitting the DB on every page load. Idempotent
        // regardless — the inner method short-circuits per-client.
        $launcherSecret = OEGlobalsBag::getInstance()->getString('copilot_oauth_client_secret');
        $standaloneSecret = OEGlobalsBag::getInstance()
            ->getString('copilot_oauth_standalone_client_secret');
        if ($launcherSecret === '' || $standaloneSecret === '') {
            try {
                $registration = new CopilotClientRegistration(
                    db: new QueryUtilsExecutor(),
                    logger: $logger,
                    copilotAppUrl: $copilotAppUrl,
                    agentBackendUrl: $agentBackendUrl,
                    secretGenerator: static fn (): string => bin2hex(random_bytes(32)),
                );
                $registration->ensureRegistered();
            } catch (\RuntimeException | \LogicException $e) {
                // Don't crash the page lifecycle if the DB isn't ready yet
                // (e.g. during install). The admin UI can rerun the registration.
                $logger->warning('Co-Pilot client registration deferred', ['exception' => $e]);
            }
        }

        // Best-effort: seed the demo non-admin provider used by standalone
        // login flow demos and CareTeam-gate evals. Idempotent — the seeder
        // short-circuits if the row already exists, so this is safe to leave
        // in the page-load path (it's a single SELECT in the steady state).
        $demoPassword = OEGlobalsBag::getInstance()->getString('copilot_demo_user_password');
        if ($demoPassword === '') {
            $demoPassword = 'dr_smith_pass';
        }
        try {
            $seeder = new DemoUserSeeder(
                db: new QueryUtilsExecutor(),
                logger: $logger,
                password: $demoPassword,
                passwordHasher: static function (string $raw): string {
                    $copy = $raw; // AuthHash::passwordHash takes by reference
                    return (new AuthHash())->passwordHash($copy);
                },
                uuidGenerator: static fn (): string => random_bytes(16),
            );
            $seeder->ensureSeeded();
        } catch (\RuntimeException | \LogicException $e) {
            // Mirrors the registration path: the install flow may run before
            // the users/users_secure tables are ready. Logged, not fatal.
            $logger->warning('Co-Pilot demo user seed deferred', ['exception' => $e]);
        }
    }

    public function onRestApiCreate(RestApiCreateEvent $event): void
    {
        $event->addToRouteMap(
            'POST /api/patient/:pid/lab_result',
            static function ($pid, HttpRestRequest $request) {
                RestConfig::request_authorization_check($request, 'patients', 'lab', ['write', 'addonly']);
                QueryUtils::sqlStatementThrowException(self::LAB_RESULT_MAP_SQL, []);
                $body = json_decode((string) file_get_contents('php://input'), true);
                $payload = is_array($body) ? $body : [];
                $writer = new LabResultWriter(
                    db: new LabWriterQueryUtilsExecutor(),
                    uuidGenerator: static fn (): string => random_bytes(16),
                );
                return (new LabResultApiController($writer))->post($pid, $payload);
            }
        );
    }
}
