--
-- Co-Pilot Launcher Module — Uninstall
--
-- Drops only the audit table. The oauth_clients row is left in place so a
-- re-install does not regenerate keys; admins can disable/delete via the
-- API Clients UI explicitly.
--
-- @package   OpenEMR
-- @link      https://www.open-emr.org
-- @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
--

#IfTable agent_audit
DROP TABLE `agent_audit`;
#EndIf
