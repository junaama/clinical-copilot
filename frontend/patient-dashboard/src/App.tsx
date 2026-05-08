import type { PatientDashboardConfig } from './types';
import PatientHeader from './components/PatientHeader';

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
        <p>Loading clinical data...</p>
      </main>
    </div>
  );
}
