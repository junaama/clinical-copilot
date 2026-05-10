import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import CareTeamCard from '../components/CareTeamCard';
import type { FhirBundle, FhirCareTeam } from '../fhir-types';
import type { CareTeamEditConfig } from '../types';

const FHIR_BASE = '/openemr/apis/default/fhir/r4';
const PATIENT_UUID = 'test-uuid-123';
const WEB_ROOT = '/openemr';
const CSRF_TOKEN = 'csrf-test-token-123';
const SAVE_URL = '/openemr/interface/patient_file/summary/demographics.php';

const FIXTURE_BUNDLE: FhirBundle<FhirCareTeam> = {
  resourceType: 'Bundle',
  type: 'searchset',
  entry: [
    {
      resource: {
        resourceType: 'CareTeam',
        id: 'ct-1',
        status: 'active',
        name: 'Primary Care Team',
        participant: [
          {
            role: [{ coding: [{ display: 'Physician' }] }],
            member: { reference: 'Practitioner/pr-1', display: 'Dr. Smith' },
            onBehalfOf: { reference: 'Organization/org-1', display: 'Main Clinic' },
            period: { start: '2023-01-15' },
          },
          {
            role: [{ text: 'Caregiver' }],
            member: { reference: 'RelatedPerson/rp-1', display: 'Jane Doe' },
          },
        ],
      },
    },
  ],
};

const EMPTY_BUNDLE: FhirBundle<FhirCareTeam> = {
  resourceType: 'Bundle',
  type: 'searchset',
};

const EDIT_CONFIG: CareTeamEditConfig = {
  teamId: 1,
  teamName: 'Primary Care Team',
  teamStatus: 'active',
  users: [
    { id: 10, name: 'Smith, John', physicianType: 'MD' },
    { id: 20, name: 'Jones, Sarah', physicianType: 'NP' },
  ],
  relatedPersons: [
    { id: 100, name: 'Jane Doe', relationship: 'Spouse' },
  ],
  facilities: [
    { id: 1, name: 'Main Clinic' },
    { id: 2, name: 'Downtown Hospital' },
  ],
  roles: [
    { id: 'physician', title: 'Physician' },
    { id: 'nurse', title: 'Nurse' },
    { id: 'caregiver', title: 'Caregiver' },
  ],
  statuses: [
    { id: 'active', title: 'Active' },
    { id: 'inactive', title: 'Inactive' },
  ],
  existingMembers: [
    {
      memberType: 'user',
      userId: 10,
      contactId: null,
      role: 'physician',
      facilityId: 1,
      providerSince: '2023-01-15',
      status: 'active',
      note: null,
      userName: 'Smith, John',
      contactName: null,
    },
  ],
};

function mockFetch(response: unknown): void {
  vi.spyOn(globalThis, 'fetch').mockResolvedValue({
    ok: true,
    json: () => Promise.resolve(response),
  } as Response);
}

describe('CareTeamCard', () => {
  afterEach(() => vi.restoreAllMocks());

  describe('view mode', () => {
    it('renders care team members when data is loaded', async () => {
      mockFetch(FIXTURE_BUNDLE);

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
        />,
      );

      await waitFor(() => {
        expect(screen.getByText('Dr. Smith')).toBeInTheDocument();
      });

      expect(screen.getByText('Jane Doe')).toBeInTheDocument();
      expect(screen.getByText(/Physician/)).toBeInTheDocument();
      expect(screen.getByText(/Caregiver/)).toBeInTheDocument();
    });

    it('does not render when bundle has no entries', async () => {
      mockFetch(EMPTY_BUNDLE);

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
        />,
      );

      await waitFor(() => {
        expect(screen.queryByTestId('card-care-team')).not.toBeInTheDocument();
        expect(screen.queryByText(/no care team recorded/i)).not.toBeInTheDocument();
      });
    });

    it('shows loading state initially', () => {
      vi.spyOn(globalThis, 'fetch').mockReturnValue(new Promise(() => {}));

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
        />,
      );

      expect(screen.getByRole('status')).toBeInTheDocument();
    });

    it('shows error state on fetch failure', async () => {
      vi.spyOn(globalThis, 'fetch').mockResolvedValue({
        ok: false,
        status: 500,
        json: () => Promise.resolve({}),
      } as Response);

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
        />,
      );

      await waitFor(() => {
        expect(screen.getByRole('alert')).toBeInTheDocument();
      });
    });

    it('renders an edit link pointing to legacy page', async () => {
      mockFetch(FIXTURE_BUNDLE);

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
        />,
      );

      await waitFor(() => {
        expect(screen.getByText('Dr. Smith')).toBeInTheDocument();
      });

      const editLink = screen.getByRole('link', { name: /edit/i });
      expect(editLink.getAttribute('href')).toContain(WEB_ROOT);
    });

    it('displays facility and since date for providers', async () => {
      mockFetch(FIXTURE_BUNDLE);

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
        />,
      );

      await waitFor(() => {
        expect(screen.getByText('Dr. Smith')).toBeInTheDocument();
      });

      expect(screen.getByText(/Main Clinic/)).toBeInTheDocument();
      expect(screen.getByText(/2023-01-15/)).toBeInTheDocument();
    });
  });

  describe('edit mode', () => {
    it('enters edit mode when edit button is clicked', async () => {
      mockFetch(FIXTURE_BUNDLE);
      const user = userEvent.setup();

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
          editConfig={EDIT_CONFIG}
        />,
      );

      await waitFor(() => {
        expect(screen.getByText('Dr. Smith')).toBeInTheDocument();
      });

      const editButton = screen.getByRole('button', { name: /edit care team/i });
      await user.click(editButton);

      expect(screen.getByText(/team name/i)).toBeInTheDocument();
    });

    it('does not show edit button when editConfig is absent', async () => {
      mockFetch(FIXTURE_BUNDLE);

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
        />,
      );

      await waitFor(() => {
        expect(screen.getByText('Dr. Smith')).toBeInTheDocument();
      });

      expect(screen.queryByRole('button', { name: /edit care team/i })).not.toBeInTheDocument();
    });

    it('cancels edit mode and returns to view', async () => {
      mockFetch(FIXTURE_BUNDLE);
      const user = userEvent.setup();

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
          editConfig={EDIT_CONFIG}
        />,
      );

      await waitFor(() => {
        expect(screen.getByText('Dr. Smith')).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /edit care team/i }));
      expect(screen.getByText(/team name/i)).toBeInTheDocument();

      await user.click(screen.getByRole('button', { name: /cancel/i }));

      // Should be back in view mode — Dr. Smith rendered in a list, not in a form
      expect(screen.getByText('Dr. Smith')).toBeInTheDocument();
      expect(screen.queryByText(/team name/i)).not.toBeInTheDocument();
    });

    it('adds a provider row', async () => {
      mockFetch(FIXTURE_BUNDLE);
      const user = userEvent.setup();

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
          editConfig={EDIT_CONFIG}
        />,
      );

      await waitFor(() => {
        expect(screen.getByText('Dr. Smith')).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /edit care team/i }));

      // Count rows before add
      const rowsBefore = screen.getAllByTestId('member-row');
      const countBefore = rowsBefore.length;

      await user.click(screen.getByRole('button', { name: /add provider/i }));

      const rowsAfter = screen.getAllByTestId('member-row');
      expect(rowsAfter.length).toBe(countBefore + 1);
    });

    it('adds a related person row', async () => {
      mockFetch(FIXTURE_BUNDLE);
      const user = userEvent.setup();

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
          editConfig={EDIT_CONFIG}
        />,
      );

      await waitFor(() => {
        expect(screen.getByText('Dr. Smith')).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /edit care team/i }));

      const rowsBefore = screen.getAllByTestId('member-row');
      const countBefore = rowsBefore.length;

      await user.click(screen.getByRole('button', { name: /add related person/i }));

      const rowsAfter = screen.getAllByTestId('member-row');
      expect(rowsAfter.length).toBe(countBefore + 1);
    });

    it('removes a member row', async () => {
      mockFetch(FIXTURE_BUNDLE);
      const user = userEvent.setup();

      // Use a config with two members so removing one still leaves rows queryable
      const twoMemberConfig: CareTeamEditConfig = {
        ...EDIT_CONFIG,
        existingMembers: [
          ...EDIT_CONFIG.existingMembers,
          {
            memberType: 'user',
            userId: 20,
            contactId: null,
            role: 'nurse',
            facilityId: null,
            providerSince: null,
            status: 'active',
            note: null,
            userName: 'Jones, Sarah',
            contactName: null,
          },
        ],
      };

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
          editConfig={twoMemberConfig}
        />,
      );

      await waitFor(() => {
        expect(screen.getByText('Dr. Smith')).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /edit care team/i }));

      const rowsBefore = screen.getAllByTestId('member-row');
      expect(rowsBefore.length).toBe(2);

      // Click the first remove button
      const removeButtons = screen.getAllByRole('button', { name: /remove/i });
      await user.click(removeButtons[0]);

      const rowsAfter = screen.getAllByTestId('member-row');
      expect(rowsAfter.length).toBe(1);
    });

    it('submits the form via POST with correct payload shape', async () => {
      const fetchSpy = vi.spyOn(globalThis, 'fetch');
      // First call returns FHIR bundle for view mode
      fetchSpy.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve(FIXTURE_BUNDLE),
      } as Response);
      // Second call handles the save POST
      fetchSpy.mockResolvedValueOnce({
        ok: true,
        json: () => Promise.resolve({ success: true }),
      } as Response);

      const user = userEvent.setup();

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
          editConfig={EDIT_CONFIG}
        />,
      );

      await waitFor(() => {
        expect(screen.getByText('Dr. Smith')).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /edit care team/i }));
      await user.click(screen.getByRole('button', { name: /save/i }));

      await waitFor(() => {
        // The save POST should have been called
        const saveCalls = fetchSpy.mock.calls.filter(
          (call) => {
            const url = typeof call[0] === 'string' ? call[0] : '';
            return url.includes('demographics.php');
          },
        );
        expect(saveCalls.length).toBe(1);
      });

      // Verify the POST request shape
      const saveCall = fetchSpy.mock.calls.find(
        (call) => {
          const url = typeof call[0] === 'string' ? call[0] : '';
          return url.includes('demographics.php');
        },
      );
      expect(saveCall).toBeDefined();

      const init = saveCall![1] as RequestInit;
      expect(init.method).toBe('POST');
      expect(init.credentials).toBe('same-origin');

      // Body should be FormData with required fields
      const body = init.body as FormData;
      expect(body.get('save_care_team')).toBe('true');
      expect(body.get('csrf_token_form')).toBe(CSRF_TOKEN);
      expect(body.get('team_name')).toBe('Primary Care Team');
      expect(body.get('team_id')).toBe('1');
    });

    it('shows existing members in edit form', async () => {
      mockFetch(FIXTURE_BUNDLE);
      const user = userEvent.setup();

      render(
        <CareTeamCard
          fhirBaseUrl={FHIR_BASE}
          patientUuid={PATIENT_UUID}
          webRoot={WEB_ROOT}
          csrfToken={CSRF_TOKEN}
          saveUrl={SAVE_URL}
          editConfig={EDIT_CONFIG}
        />,
      );

      await waitFor(() => {
        expect(screen.getByText('Dr. Smith')).toBeInTheDocument();
      });

      await user.click(screen.getByRole('button', { name: /edit care team/i }));

      // Should have at least one member row from existing members
      const rows = screen.getAllByTestId('member-row');
      expect(rows.length).toBeGreaterThanOrEqual(1);
    });
  });
});
