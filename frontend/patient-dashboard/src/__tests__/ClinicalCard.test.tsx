import { render, screen } from '@testing-library/react';
import ClinicalCard from '../components/ClinicalCard';

describe('ClinicalCard', () => {
  it('renders title and children when loaded', () => {
    render(
      <ClinicalCard title="Allergies" loading={false} error={null} editUrl="/edit/allergies">
        <p>Penicillin</p>
      </ClinicalCard>,
    );

    expect(screen.getByText('Allergies')).toBeInTheDocument();
    expect(screen.getByText('Penicillin')).toBeInTheDocument();
  });

  it('shows loading state', () => {
    render(
      <ClinicalCard title="Allergies" loading={true} error={null} editUrl="/edit/allergies">
        <p>Should not show</p>
      </ClinicalCard>,
    );

    expect(screen.getByRole('status')).toHaveTextContent('Loading');
    expect(screen.queryByText('Should not show')).not.toBeInTheDocument();
  });

  it('shows error state', () => {
    render(
      <ClinicalCard title="Allergies" loading={false} error="Network error" editUrl="/edit/allergies">
        <p>Should not show</p>
      </ClinicalCard>,
    );

    expect(screen.getByRole('alert')).toHaveTextContent('Network error');
    expect(screen.queryByText('Should not show')).not.toBeInTheDocument();
  });

  it('renders edit link to legacy page', () => {
    render(
      <ClinicalCard title="Allergies" loading={false} error={null} editUrl="/openemr/interface/allergy.php">
        <p>Content</p>
      </ClinicalCard>,
    );

    const link = screen.getByRole('link', { name: /edit/i });
    expect(link).toHaveAttribute('href', '/openemr/interface/allergy.php');
  });

  it('does not render when empty and not loading', () => {
    render(
      <ClinicalCard title="Allergies" loading={false} error={null} editUrl="/edit" isEmpty={true}>
        {null}
      </ClinicalCard>,
    );

    expect(screen.queryByTestId('card-allergies')).not.toBeInTheDocument();
    expect(screen.queryByText(/no .* recorded/i)).not.toBeInTheDocument();
  });
});
