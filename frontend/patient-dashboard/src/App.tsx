import type { PatientDashboardConfig } from './types';
import PatientHeader from './components/PatientHeader';
import AllergyCard from './components/AllergyCard';
import ProblemListCard from './components/ProblemListCard';
import MedicationCard from './components/MedicationCard';
import EncounterHistoryCard from './components/EncounterHistoryCard';
import CareTeamCard from './components/CareTeamCard';

function getConfig(): PatientDashboardConfig | null {
  return window.__OPENEMR_PATIENT_DASHBOARD__ ?? null;
}

export default function App() {
  const config = getConfig();

  if (!config) {
    return (
      <div className="dashboard-error" role="alert">
        <p>Patient dashboard configuration is missing. Please access this page through OpenEMR.</p>
      </div>
    );
  }

  return (
    <div className="patient-dashboard" data-testid="patient-dashboard">
      <header className="dashboard-header">
        <h1>Patient Dashboard</h1>
        <nav className="dashboard-nav">
          <a
            href={config.legacyDashboardUrl}
            className="legacy-link"
            data-testid="legacy-dashboard-link"
          >
            View Legacy Dashboard
          </a>
        </nav>
      </header>

      <PatientHeader
        fhirBaseUrl={config.fhirBaseUrl}
        patientUuid={config.patientUuid}
      />

      <main className="dashboard-main">
        <div className="dashboard-cards">
          <AllergyCard
            fhirBaseUrl={config.fhirBaseUrl}
            patientUuid={config.patientUuid}
            webRoot={config.webRoot}
          />
          <ProblemListCard
            fhirBaseUrl={config.fhirBaseUrl}
            patientUuid={config.patientUuid}
            webRoot={config.webRoot}
          />
          <MedicationCard
            title="Medications"
            fhirBaseUrl={config.fhirBaseUrl}
            patientUuid={config.patientUuid}
            webRoot={config.webRoot}
          />
          <MedicationCard
            title="Prescriptions"
            fhirBaseUrl={config.fhirBaseUrl}
            patientUuid={config.patientUuid}
            webRoot={config.webRoot}
          />
          <EncounterHistoryCard
            fhirBaseUrl={config.fhirBaseUrl}
            patientUuid={config.patientUuid}
            webRoot={config.webRoot}
          />
          <CareTeamCard
            fhirBaseUrl={config.fhirBaseUrl}
            patientUuid={config.patientUuid}
            webRoot={config.webRoot}
            csrfToken={config.csrfToken}
            saveUrl={config.modernDashboardUrl}
            editConfig={config.careTeamEdit}
          />
        </div>
      </main>
    </div>
  );
}
