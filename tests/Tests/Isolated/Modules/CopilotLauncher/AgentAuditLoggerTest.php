<?php

/**
 * AgentAuditLogger isolated test.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

namespace OpenEMR\Tests\Isolated\Modules\CopilotLauncher;

require_once __DIR__ . '/_module_autoload.php';

use OpenEMR\Modules\CopilotLauncher\Domain\AuditDecision;
use OpenEMR\Modules\CopilotLauncher\Domain\AuditEntry;
use OpenEMR\Modules\CopilotLauncher\Domain\ConversationId;
use OpenEMR\Modules\CopilotLauncher\Domain\PatientPid;
use OpenEMR\Modules\CopilotLauncher\Service\AgentAuditLogger;
use PHPUnit\Framework\TestCase;
use Psr\Log\NullLogger;

final class AgentAuditLoggerTest extends TestCase
{
    public function testRecordWritesAllFieldsAndReturnsInsertId(): void
    {
        $db = new InMemoryExecutor();
        $db->nextInsertId = 42;
        $sut = new AgentAuditLogger($db, new NullLogger());

        $entry = new AuditEntry(
            conversationId: new ConversationId('demo-1'),
            turn: 3,
            pid: new PatientPid(7),
            userId: 11,
            decision: AuditDecision::Allow,
            workflowId: 'W-2',
            classifierConfidence: 0.93,
            model: 'opus-4.7',
            tokensIn: 1234,
            tokensOut: 567,
            latencyMs: 4321,
            costUsd: 0.05,
        );

        $id = $sut->record($entry);

        $this->assertSame(42, $id);
        $this->assertCount(1, $db->log);
        $this->assertSame('insert', $db->log[0]['type']);
        $this->assertStringContainsString('INSERT INTO `agent_audit`', $db->log[0]['sql']);
        $binds = $db->log[0]['binds'];
        $this->assertSame('demo-1', $binds[0]);
        $this->assertSame(3, $binds[1]);
        $this->assertSame(7, $binds[2]);
        $this->assertSame(11, $binds[3]);
        $this->assertSame('W-2', $binds[4]);
        $this->assertSame(0.93, $binds[5]);
        $this->assertSame('allow', $binds[6]);
        $this->assertSame(0, $binds[13], 'break_glass defaults to 0');
    }

    public function testRecordRejectsConfidenceOutsideUnitInterval(): void
    {
        $this->expectException(\DomainException::class);
        new AuditEntry(
            conversationId: new ConversationId('c'),
            turn: 1,
            pid: new PatientPid(1),
            userId: 1,
            decision: AuditDecision::ToolFailure,
            classifierConfidence: 1.5,
        );
    }

    public function testRecordPropagatesDatabaseFailure(): void
    {
        $db = new InMemoryExecutor();
        $db->shouldThrowOnInsert = true;
        $sut = new AgentAuditLogger($db, new NullLogger());

        $this->expectException(\RuntimeException::class);
        $sut->record(new AuditEntry(
            conversationId: new ConversationId('c'),
            turn: 1,
            pid: new PatientPid(1),
            userId: 1,
            decision: AuditDecision::DeniedAuthz,
        ));
    }

    public function testDecisionFromWireRoundTripsKnownValues(): void
    {
        $this->assertSame(AuditDecision::Allow, AuditDecision::fromWire('allow'));
        $this->assertSame(AuditDecision::DeniedAuthz, AuditDecision::fromWire('denied_authz'));
        $this->expectException(\DomainException::class);
        AuditDecision::fromWire('mystery_meat');
    }
}
