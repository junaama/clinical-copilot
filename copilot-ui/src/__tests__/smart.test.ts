import { describe, expect, it } from 'vitest';
import { exchangeLaunchForToken, parseSmartLaunch } from '../api/smart';

describe('parseSmartLaunch', () => {
  it('reads iss and launch from the query string', () => {
    const ctx = parseSmartLaunch(
      'https://app.example.com/?iss=https%3A%2F%2Ffhir.example.com&launch=abc123',
    );
    expect(ctx.iss).toBe('https://fhir.example.com');
    expect(ctx.launch).toBe('abc123');
    expect(ctx.accessToken).toBe('');
  });

  it('reads patient and user from query', () => {
    const ctx = parseSmartLaunch(
      'https://app.example.com/?patient=4&user=naama',
    );
    expect(ctx.patientId).toBe('4');
    expect(ctx.userId).toBe('naama');
  });

  it('falls back to patient_id / user_id aliases', () => {
    const ctx = parseSmartLaunch(
      'https://app.example.com/?patient_id=42&user_id=admin',
    );
    expect(ctx.patientId).toBe('42');
    expect(ctx.userId).toBe('admin');
  });

  it('reads access_token from the URL fragment', () => {
    const ctx = parseSmartLaunch(
      'https://app.example.com/#access_token=Bearer%20xyz&token_type=bearer',
    );
    expect(ctx.accessToken).toBe('Bearer xyz');
  });

  it('returns an empty context for invalid URLs', () => {
    const ctx = parseSmartLaunch('not a url');
    expect(ctx.iss).toBeNull();
    expect(ctx.launch).toBeNull();
    expect(ctx.patientId).toBeNull();
    expect(ctx.userId).toBeNull();
    expect(ctx.accessToken).toBe('');
  });

  it('returns an empty context when no params are present', () => {
    const ctx = parseSmartLaunch('https://app.example.com/');
    expect(ctx.iss).toBeNull();
    expect(ctx.launch).toBeNull();
    expect(ctx.conversationId).toBeNull();
  });

  it('reads conversation_id issued by the backend /smart/callback redirect', () => {
    const ctx = parseSmartLaunch(
      'https://app.example.com/?conversation_id=conv-123&patient=p1',
    );
    expect(ctx.conversationId).toBe('conv-123');
    expect(ctx.patientId).toBe('p1');
  });
});

describe('exchangeLaunchForToken', () => {
  it('rejects with an explicit not-implemented message (backend handles v1)', async () => {
    await expect(
      exchangeLaunchForToken({
        iss: 'https://fhir.example.com',
        launch: 'abc',
        patientId: null,
        userId: null,
        accessToken: '',
        conversationId: null,
      }),
    ).rejects.toThrow(/not implemented/);
  });
});
