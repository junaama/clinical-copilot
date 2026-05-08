/**
 * Care Team clinical card — fetches CareTeam resources, renders view/edit modes.
 *
 * View mode: reads from the FHIR API and renders members.
 * Edit mode: uses PHP boot config metadata for form dropdowns, submits via POST.
 */

import { useState, useCallback } from 'react';
import type { FhirCareTeam } from '../fhir-types';
import type { CareTeamEditConfig, CareTeamEditMember } from '../types';
import { useFhirSearch } from '../hooks/use-fhir-search';
import { adaptCareTeams } from '../adapters/careteam-adapter';
import ClinicalCard from './ClinicalCard';

interface CareTeamCardProps {
  readonly fhirBaseUrl: string;
  readonly patientUuid: string;
  readonly webRoot: string;
  readonly csrfToken: string;
  readonly saveUrl: string;
  readonly editConfig?: CareTeamEditConfig;
}

/** Mutable row state for the edit form. */
interface EditMemberRow {
  memberType: 'user' | 'contact';
  userId: string;
  contactId: string;
  role: string;
  facilityId: string;
  providerSince: string;
  status: string;
  note: string;
}

function toEditRow(m: CareTeamEditMember): EditMemberRow {
  return {
    memberType: m.memberType,
    userId: m.userId != null ? String(m.userId) : '',
    contactId: m.contactId != null ? String(m.contactId) : '',
    role: m.role,
    facilityId: m.facilityId != null ? String(m.facilityId) : '',
    providerSince: m.providerSince ?? '',
    status: m.status,
    note: m.note ?? '',
  };
}

function emptyProviderRow(): EditMemberRow {
  return {
    memberType: 'user',
    userId: '',
    contactId: '',
    role: '',
    facilityId: '',
    providerSince: '',
    status: 'active',
    note: '',
  };
}

function emptyRelatedPersonRow(): EditMemberRow {
  return {
    memberType: 'contact',
    userId: '',
    contactId: '',
    role: '',
    facilityId: '',
    providerSince: '',
    status: 'active',
    note: '',
  };
}

export default function CareTeamCard({
  fhirBaseUrl,
  patientUuid,
  webRoot,
  csrfToken,
  saveUrl,
  editConfig,
}: CareTeamCardProps) {
  const { bundle, loading, error } = useFhirSearch<FhirCareTeam>(
    fhirBaseUrl,
    'CareTeam',
    patientUuid,
  );

  const teams = bundle ? adaptCareTeams(bundle) : [];
  const editUrl = `${webRoot}/interface/patient_file/summary/demographics_legacy.php`;

  const [editing, setEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [teamName, setTeamName] = useState(editConfig?.teamName ?? '');
  const [teamStatus, setTeamStatus] = useState(editConfig?.teamStatus ?? 'active');
  const [members, setMembers] = useState<EditMemberRow[]>(
    () => editConfig?.existingMembers.map(toEditRow) ?? [],
  );

  const handleEdit = useCallback(() => {
    // Reset form state from config when entering edit mode
    if (editConfig) {
      setTeamName(editConfig.teamName);
      setTeamStatus(editConfig.teamStatus);
      setMembers(editConfig.existingMembers.map(toEditRow));
    }
    setEditing(true);
  }, [editConfig]);

  const handleCancel = useCallback(() => {
    setEditing(false);
  }, []);

  const handleAddProvider = useCallback(() => {
    setMembers((prev) => [...prev, emptyProviderRow()]);
  }, []);

  const handleAddRelatedPerson = useCallback(() => {
    setMembers((prev) => [...prev, emptyRelatedPersonRow()]);
  }, []);

  const handleRemoveRow = useCallback((index: number) => {
    setMembers((prev) => prev.filter((_, i) => i !== index));
  }, []);

  const handleRowChange = useCallback(
    (index: number, field: keyof EditMemberRow, value: string) => {
      setMembers((prev) =>
        prev.map((row, i) => (i === index ? { ...row, [field]: value } : row)),
      );
    },
    [],
  );

  const handleSave = useCallback(async () => {
    if (!editConfig) return;

    setSaving(true);
    try {
      const formData = new FormData();
      formData.append('save_care_team', 'true');
      formData.append('csrf_token_form', csrfToken);
      formData.append('team_name', teamName);
      formData.append('team_status', teamStatus);

      if (editConfig.teamId != null) {
        formData.append('team_id', String(editConfig.teamId));
      }

      members.forEach((row, i) => {
        if (row.memberType === 'user' && row.userId) {
          formData.append(`team[${i}][user_id]`, row.userId);
        } else if (row.memberType === 'contact' && row.contactId) {
          formData.append(`team[${i}][contact_id]`, row.contactId);
        }
        formData.append(`team[${i}][role]`, row.role);
        formData.append(`team[${i}][facility_id]`, row.facilityId);
        formData.append(`team[${i}][provider_since]`, row.providerSince);
        formData.append(`team[${i}][status]`, row.status);
        formData.append(`team[${i}][note]`, row.note);
      });

      await fetch(saveUrl, {
        method: 'POST',
        credentials: 'same-origin',
        body: formData,
      });

      setEditing(false);
      // Reload the page to reflect saved changes
      window.location.reload();
    } catch {
      // Save errors are visible via page reload behavior
    } finally {
      setSaving(false);
    }
  }, [editConfig, csrfToken, teamName, teamStatus, members, saveUrl]);

  // Render edit mode
  if (editing && editConfig) {
    return (
      <section
        className="clinical-card"
        data-testid="card-care-team"
      >
        <div className="clinical-card__header">
          <h3 className="clinical-card__title">Care Team</h3>
        </div>
        <div className="clinical-card__body">
          <div className="care-team-edit">
            <div className="care-team-edit__field">
              <label htmlFor="care-team-name">Team Name</label>
              <input
                id="care-team-name"
                type="text"
                value={teamName}
                onChange={(e) => setTeamName(e.target.value)}
              />
            </div>

            <div className="care-team-edit__field">
              <label htmlFor="care-team-status">Team Status</label>
              <select
                id="care-team-status"
                value={teamStatus}
                onChange={(e) => setTeamStatus(e.target.value)}
              >
                {editConfig.statuses.map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.title}
                  </option>
                ))}
              </select>
            </div>

            <h4>Members</h4>

            {members.map((row, index) => (
              <div key={index} className="care-team-edit__row" data-testid="member-row">
                {row.memberType === 'user' ? (
                  <select
                    aria-label="Provider"
                    value={row.userId}
                    onChange={(e) => handleRowChange(index, 'userId', e.target.value)}
                  >
                    <option value="">Select provider...</option>
                    {editConfig.users.map((u) => (
                      <option key={u.id} value={u.id}>
                        {u.name}
                        {u.physicianType ? ` (${u.physicianType})` : ''}
                      </option>
                    ))}
                  </select>
                ) : (
                  <select
                    aria-label="Related Person"
                    value={row.contactId}
                    onChange={(e) => handleRowChange(index, 'contactId', e.target.value)}
                  >
                    <option value="">Select related person...</option>
                    {editConfig.relatedPersons.map((rp) => (
                      <option key={rp.id} value={rp.id}>
                        {rp.name}
                        {rp.relationship ? ` (${rp.relationship})` : ''}
                      </option>
                    ))}
                  </select>
                )}

                <select
                  aria-label="Role"
                  value={row.role}
                  onChange={(e) => handleRowChange(index, 'role', e.target.value)}
                >
                  <option value="">Select role...</option>
                  {editConfig.roles.map((r) => (
                    <option key={r.id} value={r.id}>
                      {r.title}
                    </option>
                  ))}
                </select>

                <select
                  aria-label="Facility"
                  value={row.facilityId}
                  onChange={(e) => handleRowChange(index, 'facilityId', e.target.value)}
                >
                  <option value="">Select facility...</option>
                  {editConfig.facilities.map((f) => (
                    <option key={f.id} value={f.id}>
                      {f.name}
                    </option>
                  ))}
                </select>

                <button
                  type="button"
                  onClick={() => handleRemoveRow(index)}
                  aria-label="Remove"
                  className="care-team-edit__remove-btn"
                >
                  Remove
                </button>
              </div>
            ))}

            <div className="care-team-edit__actions">
              <button type="button" onClick={handleAddProvider}>
                Add Provider
              </button>
              <button type="button" onClick={handleAddRelatedPerson}>
                Add Related Person
              </button>
            </div>

            <div className="care-team-edit__submit">
              <button type="button" onClick={handleSave} disabled={saving}>
                {saving ? 'Saving...' : 'Save Care Team'}
              </button>
              <button type="button" onClick={handleCancel} disabled={saving}>
                Cancel
              </button>
            </div>
          </div>
        </div>
      </section>
    );
  }

  // Render view mode
  const allMembers = teams.flatMap((t) => t.members);

  return (
    <ClinicalCard
      title="Care Team"
      loading={loading}
      error={error}
      editUrl={editUrl}
      isEmpty={allMembers.length === 0}
    >
      {editConfig && (
        <button
          type="button"
          onClick={handleEdit}
          className="care-team__edit-btn"
          aria-label="Edit Care Team"
        >
          Edit Care Team
        </button>
      )}
      <ul className="clinical-card__list">
        {allMembers.map((member, index) => (
          <li key={`${member.name}-${index}`} className="clinical-card__list-item">
            <div className="clinical-card__item-header">
              <strong>{member.name}</strong>
              <span className={`clinical-card__badge clinical-card__badge--${member.memberType}`}>
                {member.role}
              </span>
            </div>
            <div className="clinical-card__item-details">
              {member.facility && <span>Facility: {member.facility}</span>}
              {member.since && <span>Since: {member.since}</span>}
            </div>
          </li>
        ))}
      </ul>
    </ClinicalCard>
  );
}
