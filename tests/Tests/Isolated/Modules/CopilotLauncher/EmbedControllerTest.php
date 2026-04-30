<?php

/**
 * EmbedController isolated test — exercises input validation only; the
 * actual SMART launch token minting is tested upstream in the existing
 * SmartLaunchController tests.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\CopilotLauncher;

require_once __DIR__ . '/_module_autoload.php';

use OpenEMR\Modules\CopilotLauncher\Controller\EmbedController;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;

final class EmbedControllerTest extends TestCase
{
    public function testReturns400WhenPidMissing(): void
    {
        $sut = $this->makeController();
        $result = $sut->render([], ['pid' => 1, 'authUserID' => 5]);
        $this->assertFalse($result->isOk());
        $this->assertSame(400, $result->statusCode);
    }

    public function testReturns400WhenPidNotPositiveInteger(): void
    {
        $sut = $this->makeController();
        $result = $sut->render(['pid' => 'abc'], ['pid' => 1, 'authUserID' => 5]);
        $this->assertSame(400, $result->statusCode);
    }

    public function testReturns401WhenSessionHasNoPid(): void
    {
        $sut = $this->makeController();
        $result = $sut->render(['pid' => '7'], ['pid' => null, 'authUserID' => 5]);
        $this->assertSame(401, $result->statusCode);
    }

    public function testReturns403WhenRequestedPidDoesNotMatchSession(): void
    {
        $sut = $this->makeController();
        $result = $sut->render(['pid' => '8'], ['pid' => 7, 'authUserID' => 5]);
        $this->assertSame(403, $result->statusCode);
    }

    public function testReturns401WhenNoAuthenticatedUser(): void
    {
        $sut = $this->makeController();
        $result = $sut->render(['pid' => '7'], ['pid' => 7, 'authUserID' => null]);
        $this->assertSame(401, $result->statusCode);
    }

    public function testReturnsOkWhenSessionAndQueryAgree(): void
    {
        $sut = $this->makeController();
        $result = $sut->render(['pid' => '7'], ['pid' => 7, 'authUserID' => '5']);
        $this->assertTrue($result->isOk());
        $this->assertNotNull($result->pid);
        $this->assertSame(7, $result->pid->value);
        $this->assertSame('5', $result->userId);
    }

    private function makeController(): EmbedController
    {
        return new EmbedController(
            logger: new NullLogger(),
            copilotAppUrl: 'http://copilot.example',
            issuer: 'http://openemr.example/apis/default/fhir',
        );
    }
}
