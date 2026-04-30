<?php

/**
 * ChartSidebarListener
 *
 * Injects the Co-Pilot launch markup at the top of the patient demographics
 * section list. The markup itself is a single button + an empty iframe host
 * that the browser-side bridge fills in on click. The button URL points at
 * the module's public/embed.php which performs the SMART EHR launch.
 *
 * Why TOP: docking on the right edge of the chart needs a stable mount point
 * inside the demographics column. EVENT_SECTION_LIST_RENDER_TOP fires once,
 * before any chart-card has been rendered, which means the bridge JS — also
 * injected here — can attach data-card="..." attributes to subsequent card
 * containers reliably (see `assignDataCardAttributes` in the inline script).
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
        private string $installPath,
        private string $copilotAppUrl,
    ) {
    }

    public function onPatientSummaryTop(RenderEvent $event): void
    {
        $pid = $event->getPid();
        if ($pid === null || $pid === 0) {
            return;
        }
        $embedUrl = $this->installPath . '/public/embed.php?pid=' . urlencode((string) $pid);
        $expectedOrigin = $this->originOf($this->copilotAppUrl);

        // attr() is loaded by globals.php in the host context.
        $launchUrlAttr = function_exists('attr') ? attr($embedUrl) : htmlspecialchars($embedUrl, ENT_QUOTES);
        $originJs = json_encode($expectedOrigin, JSON_UNESCAPED_SLASHES | JSON_THROW_ON_ERROR);

        // Markup + bridge: launch button, mount point for the iframe, and
        // the postMessage listener that turns copilot:flash-card events into
        // a flash outline on the matching chart card.
        echo <<<HTML
<section class="copilot-launcher" id="copilot-launcher-mount" data-copilot-origin="{$expectedOrigin}">
    <button type="button" class="btn btn-primary btn-sm" id="copilot-open-btn"
            data-embed-url="{$launchUrlAttr}">
        Open Co-Pilot
    </button>
    <div id="copilot-iframe-slot" hidden></div>
</section>
<style>
    .copilot-launcher { margin: 0 0 12px 0; }
    #copilot-iframe-slot { position: fixed; right: 0; top: 0; width: 420px;
        height: 100vh; z-index: 9000; border-left: 1px solid #ccc;
        background: #fff; box-shadow: -4px 0 12px rgba(0,0,0,.08); }
    #copilot-iframe-slot iframe { width: 100%; height: 100%; border: 0; }
    /* Mirrors the .emr-card.flash rule from copilot-ui/src/styles/styles.css */
    [data-card].copilot-flash { animation: copilot-flash 1.6s ease-out 1; outline-offset: 4px; }
    @keyframes copilot-flash {
        0%   { outline: 3px solid rgba(255, 193, 7, 0); }
        20%  { outline: 3px solid rgba(255, 193, 7, .9); }
        100% { outline: 3px solid rgba(255, 193, 7, 0); }
    }
</style>
<script>
(function () {
    var EXPECTED_ORIGIN = {$originJs};
    var KNOWN_CARDS = [
        'vitals','labs','medications','problems','allergies',
        'prescriptions','encounters','documents','other'
    ];

    /**
     * Heuristic: OpenEMR's stock chart cards do not carry stable
     * data-card markers. We map by the section heading text, which is
     * the most stable identifier in the upstream UI today. If the
     * heading text is restyled upstream, update this map; the rest of
     * the bridge is data-driven by the data-card attribute.
     */
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

    function openCoPilot() {
        var slot = document.getElementById('copilot-iframe-slot');
        var btn = document.getElementById('copilot-open-btn');
        if (!slot || !btn) return;
        if (slot.firstChild) { slot.hidden = !slot.hidden; return; }
        var iframe = document.createElement('iframe');
        iframe.src = btn.getAttribute('data-embed-url');
        iframe.title = 'Clinical Co-Pilot';
        iframe.setAttribute('allow', 'clipboard-write');
        slot.appendChild(iframe);
        slot.hidden = false;
    }

    document.addEventListener('DOMContentLoaded', function () {
        assignDataCardAttributes();
        var btn = document.getElementById('copilot-open-btn');
        if (btn) btn.addEventListener('click', openCoPilot);
    });

    window.addEventListener('message', function (event) {
        if (event.origin !== EXPECTED_ORIGIN) return;
        var data = event.data;
        if (!data || data.type !== 'copilot:flash-card') return;
        if (typeof data.card !== 'string') return;
        flashCard(data.card);
    }, false);
})();
</script>
HTML;
    }

    private function originOf(string $url): string
    {
        $parts = parse_url($url);
        if ($parts === false) {
            // Defensive default — treat as a same-origin null so messages from
            // a misconfigured app are dropped, not trusted.
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
