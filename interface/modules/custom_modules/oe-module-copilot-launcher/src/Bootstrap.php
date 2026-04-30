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
use OpenEMR\Core\OEGlobalsBag;
use OpenEMR\Events\PatientDemographics\RenderEvent;
use OpenEMR\Modules\CopilotLauncher\Listeners\ChartSidebarListener;
use OpenEMR\Modules\CopilotLauncher\Service\CopilotClientRegistration;
use OpenEMR\Modules\CopilotLauncher\Service\QueryUtilsExecutor;
use Symfony\Component\EventDispatcher\EventDispatcherInterface;

final class Bootstrap
{
    public const MODULE_INSTALLATION_PATH = '/interface/modules/custom_modules/oe-module-copilot-launcher';
    public const MODULE_NAME = 'oe-module-copilot-launcher';

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
        $sidebar = new ChartSidebarListener(
            installPath: OEGlobalsBag::getInstance()->getWebRoot() . self::MODULE_INSTALLATION_PATH,
            copilotAppUrl: $copilotAppUrl,
        );

        // Renders the launch + iframe-host markup in the patient summary view.
        $this->eventDispatcher->addListener(
            RenderEvent::EVENT_SECTION_LIST_RENDER_TOP,
            $sidebar->onPatientSummaryTop(...)
        );

        // Best-effort: ensure the SMART client has a generated secret. Skipped
        // entirely when globals.copilot_oauth_client_secret is already set,
        // to avoid hitting the DB on every page load. Idempotent regardless.
        if (OEGlobalsBag::getInstance()->getString('copilot_oauth_client_secret') === '') {
            try {
                $registration = new CopilotClientRegistration(
                    db: new QueryUtilsExecutor(),
                    logger: $logger,
                    copilotAppUrl: $copilotAppUrl,
                    secretGenerator: static fn (): string => bin2hex(random_bytes(32)),
                );
                $registration->ensureRegistered();
            } catch (\RuntimeException | \LogicException $e) {
                // Don't crash the page lifecycle if the DB isn't ready yet
                // (e.g. during install). The admin UI can rerun the registration.
                $logger->warning('Co-Pilot client registration deferred', ['exception' => $e]);
            }
        }
    }
}
