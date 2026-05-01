<?php

/**
 * ChartSidebarListener
 *
 * Injects a floating "Co-Pilot" launch button and a fixed-position sidebar
 * slot at the bottom of the patient demographics page. The button is
 * position:fixed so it sits on top of the chart and is always visible
 * regardless of how the chart is scrolled — no buried-inside-a-card UX.
 *
 * Why EVENT_RENDER_POST_PAGELOAD: it fires once at the end of demographics.php,
 * after every chart card has rendered. That gives us a stable mount point
 * for a sidebar overlay that lives outside the section list and lets the
 * inline JS attach data-card="..." to chart cards for the citation-flash
 * bridge.
 *
 * Demo-path notes (Path A in the design doc):
 *   - The iframe URL points directly at the Co-Pilot UI, NOT through
 *     embed.php / SMART OAuth. The patient and user identifiers come
 *     straight from $_SESSION (the user is already authenticated in
 *     OpenEMR; the patient is already the chart context).
 *   - The agent honors DEMO_MODE and reads patient context from the chat
 *     request body instead of looking up a SMART token bundle.
 *   - The full SMART path (embed.php + SmartLaunchToken) still exists for
 *     production deploys; nothing here breaks it.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Modules\CopilotLauncher\Listeners;

use OpenEMR\Events\PatientDemographics\RenderEvent;

final readonly class ChartSidebarListener
{
    public function __construct(
        private string $copilotAppUrl,
        private int $sessionUserId,
        private string $sessionUserName,
    ) {
    }

    public function onPatientPagePostLoad(RenderEvent $event): void
    {
        $pid = $event->getPid();
        if ($pid === null || $pid === 0) {
            return;
        }

        $copilotUrl = rtrim($this->copilotAppUrl, '/');
        $expectedOrigin = $this->originOf($copilotUrl);

        // attr() / text() loaded by globals.php in the host context.
        $copilotUrlAttr = function_exists('attr') ? attr($copilotUrl) : htmlspecialchars($copilotUrl, ENT_QUOTES);
        $pidAttr = function_exists('attr') ? attr((string) $pid) : htmlspecialchars((string) $pid, ENT_QUOTES);
        $userIdAttr = function_exists('attr') ? attr((string) $this->sessionUserId) : htmlspecialchars((string) $this->sessionUserId, ENT_QUOTES);
        $userNameAttr = function_exists('attr') ? attr($this->sessionUserName) : htmlspecialchars($this->sessionUserName, ENT_QUOTES);
        $originJs = json_encode($expectedOrigin, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);

        echo <<<HTML
<div id="copilot-launcher-root"
     data-copilot-url="{$copilotUrlAttr}"
     data-copilot-origin="{$expectedOrigin}"
     data-pid="{$pidAttr}"
     data-user-id="{$userIdAttr}"
     data-user-name="{$userNameAttr}">
    <button type="button" id="copilot-fab" aria-label="Open Clinical Co-Pilot">
        <span class="copilot-fab-icon">✦</span>
        <span class="copilot-fab-label">Co-Pilot</span>
    </button>
    <aside id="copilot-sidebar" hidden aria-label="Clinical Co-Pilot"></aside>
</div>
<style>
    #copilot-fab {
        position: fixed;
        right: 24px;
        bottom: 24px;
        z-index: 9000;
        display: inline-flex;
        align-items: center;
        gap: 8px;
        padding: 10px 18px;
        font-size: 14px;
        font-weight: 600;
        color: #fff;
        background: linear-gradient(135deg, #4abfac 0%, #2f8f7f 100%);
        border: 0;
        border-radius: 999px;
        box-shadow: 0 4px 14px rgba(47, 143, 127, .35);
        cursor: pointer;
        transition: transform .15s ease, box-shadow .15s ease;
    }
    #copilot-fab:hover { transform: translateY(-1px); box-shadow: 0 6px 18px rgba(47, 143, 127, .45); }
    #copilot-fab:active { transform: translateY(0); }
    .copilot-fab-icon { font-size: 16px; line-height: 1; }
    #copilot-sidebar {
        position: fixed;
        right: 0;
        top: 0;
        width: 440px;
        max-width: 100vw;
        height: 100vh;
        z-index: 9100;
        background: #fff;
        border-left: 1px solid rgba(0,0,0,.08);
        box-shadow: -4px 0 16px rgba(0,0,0,.10);
        animation: copilot-slide-in .25s ease-out;
    }
    @keyframes copilot-slide-in {
        from { transform: translateX(100%); }
        to   { transform: translateX(0); }
    }
    #copilot-sidebar iframe {
        width: 100%;
        height: 100%;
        border: 0;
        display: block;
    }
    /* Citation flash — mirrors copilot-ui's .emr-card.flash rule. */
    [data-card].copilot-flash { animation: copilot-flash 1.6s ease-out 1; outline-offset: 4px; }
    @keyframes copilot-flash {
        0%   { outline: 3px solid rgba(255, 193, 7, 0); }
        20%  { outline: 3px solid rgba(255, 193, 7, .9); }
        100% { outline: 3px solid rgba(255, 193, 7, 0); }
    }
</style>
<script>
(function () {
    var root = document.getElementById('copilot-launcher-root');
    if (!root) return;
    var EXPECTED_ORIGIN = {$originJs};
    var KNOWN_CARDS = [
        'vitals','labs','medications','problems','allergies',
        'prescriptions','encounters','documents','other'
    ];
    var HEADING_TO_CARD = {
        'vitals': 'vitals',
        'lab results': 'labs',
        'labs': 'labs',
        'medications': 'medications',
        'medication list': 'medications',
        'medical problems': 'problems',
        'problems': 'problems',
        'allergies': 'allergies',
        'prescriptions': 'prescriptions',
        'encounters': 'encounters',
        'patient documents': 'documents',
        'documents': 'documents'
    };

    function assignDataCardAttributes() {
        var sections = document.querySelectorAll(
            '#patient_sections .panel, #patient_sections section, ' +
            '.section-card, .card-header'
        );
        sections.forEach(function (el) {
            if (el.dataset.card) return;
            var heading = (el.querySelector('.panel-title, .card-title, h3, h4, .summary_title') || el)
                .textContent || '';
            var key = heading.trim().toLowerCase();
            for (var phrase in HEADING_TO_CARD) {
                if (Object.prototype.hasOwnProperty.call(HEADING_TO_CARD, phrase) &&
                        key.indexOf(phrase) !== -1) {
                    var card = HEADING_TO_CARD[phrase];
                    var hostCard = el.closest('[class*="card"], .panel, section') || el;
                    hostCard.setAttribute('data-card', card);
                    return;
                }
            }
        });
    }

    function flashCard(card) {
        if (KNOWN_CARDS.indexOf(card) === -1) return;
        var target = document.querySelector('[data-card="' + card + '"]');
        if (!target) return;
        target.scrollIntoView({behavior: 'smooth', block: 'center'});
        target.classList.remove('copilot-flash');
        // restart animation
        // eslint-disable-next-line no-unused-expressions
        target.offsetWidth;
        target.classList.add('copilot-flash');
        setTimeout(function () { target.classList.remove('copilot-flash'); }, 2000);
    }

    function buildIframeSrc() {
        var base = root.dataset.copilotUrl;
        var qs = new URLSearchParams({
            patient: root.dataset.pid,
            user: root.dataset.userId,
            user_name: root.dataset.userName,
            surface: 'panel',
            demo: '1'
        });
        return base + '/?' + qs.toString();
    }

    function openCoPilot() {
        var sidebar = document.getElementById('copilot-sidebar');
        if (!sidebar) return;
        if (sidebar.firstChild) {
            sidebar.hidden = !sidebar.hidden;
            return;
        }
        var iframe = document.createElement('iframe');
        iframe.src = buildIframeSrc();
        iframe.title = 'Clinical Co-Pilot';
        iframe.setAttribute('allow', 'clipboard-write');
        sidebar.appendChild(iframe);
        sidebar.hidden = false;
    }

    document.addEventListener('DOMContentLoaded', function () {
        assignDataCardAttributes();
    });
    // EVENT_RENDER_POST_PAGELOAD already fires after the page is loaded — wire
    // straight away so Cmd-K and the FAB click are live immediately.
    assignDataCardAttributes();

    var btn = document.getElementById('copilot-fab');
    if (btn) btn.addEventListener('click', openCoPilot);

    document.addEventListener('keydown', function (e) {
        if ((e.metaKey || e.ctrlKey) && (e.key === 'k' || e.key === 'K')) {
            e.preventDefault();
            openCoPilot();
        }
    });

    window.addEventListener('message', function (event) {
        if (event.origin !== EXPECTED_ORIGIN) return;
        var data = event.data;
        if (!data) return;
        if (data.type === 'copilot:flash-card' && typeof data.card === 'string') {
            flashCard(data.card);
        } else if (data.type === 'copilot:close') {
            var sidebar = document.getElementById('copilot-sidebar');
            if (sidebar) sidebar.hidden = true;
        }
    }, false);
})();
</script>
HTML;
    }

    private function originOf(string $url): string
    {
        $parts = parse_url($url);
        if ($parts === false) {
            return 'null';
        }
        $scheme = $parts['scheme'] ?? '';
        $host = $parts['host'] ?? '';
        if ($scheme === '' || $host === '') {
            return 'null';
        }
        $port = isset($parts['port']) ? ':' . (int) $parts['port'] : '';
        return $scheme . '://' . $host . $port;
    }
}
