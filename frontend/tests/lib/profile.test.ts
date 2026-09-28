import { describe, expect, it, vi } from 'vitest';

import { ApiError } from '@/lib/api';
import { forgetAll, forgetField, getProfile, setField } from '@/lib/profile';
import { PROFILE_CHOICES, demoProfileField, installFakeApi } from '../helpers/fakeApi';

/** Make every request answer 200 with this body, whatever it asks. */
function serve(body: unknown): ReturnType<typeof vi.fn> {
  const mock = vi.fn(
    async () =>
      new Response(JSON.stringify(body), {
        status: 200,
        headers: { 'content-type': 'application/json' },
      }),
  );
  vi.stubGlobal('fetch', mock);
  return mock;
}

describe('getProfile', () => {
  it('returns the remembered fields and the fixed choices', async () => {
    const api = installFakeApi({ profileFields: [demoProfileField()] });
    const profile = await getProfile();

    expect(api.requests).toEqual(['GET /api/v1/profile']);
    expect(profile.fields).toEqual([demoProfileField()]);
    expect(profile.choices).toEqual(PROFILE_CHOICES);
  });

  it('returns no fields when nothing is remembered yet', async () => {
    installFakeApi();
    const profile = await getProfile();
    expect(profile.fields).toEqual([]);
    expect(profile.choices.length).toBeGreaterThan(0);
  });

  it.each([
    ['no fields array', { choices: PROFILE_CHOICES }],
    ['fields that are not a list', { fields: 'x', choices: PROFILE_CHOICES }],
    ['no choices array', { fields: [] }],
    [
      'a field with an unknown name',
      { fields: [{ ...demoProfileField(), field: 'favourite_colour' }], choices: PROFILE_CHOICES },
    ],
    [
      'values that are not a list of strings',
      { fields: [{ ...demoProfileField(), values: 'conservative' }], choices: PROFILE_CHOICES },
    ],
    [
      'labels that are not a list of strings',
      { fields: [{ ...demoProfileField(), labels: [1] }], choices: PROFILE_CHOICES },
    ],
    [
      'a quote that is not text or null',
      { fields: [{ ...demoProfileField(), quote: 5 }], choices: PROFILE_CHOICES },
    ],
    [
      'a source that is neither chat nor edited',
      { fields: [{ ...demoProfileField(), source: 'guessed' }], choices: PROFILE_CHOICES },
    ],
    [
      'a field without an updated_at',
      { fields: [{ ...demoProfileField(), updated_at: undefined }], choices: PROFILE_CHOICES },
    ],
    [
      'a choice with single not a boolean',
      { fields: [], choices: [{ ...PROFILE_CHOICES[0], single: 'yes' }] },
    ],
    [
      'a choice option without a label',
      {
        fields: [],
        choices: [{ ...PROFILE_CHOICES[0], options: [{ value: 'conservative' }] }],
      },
    ],
  ])('rejects a profile with %s', async (_label, body) => {
    serve(body);
    await expect(getProfile()).rejects.toMatchObject({ code: 'unexpected_response' });
  });

  it('rejects a profile that is not an object at all', async () => {
    serve(null);
    await expect(getProfile()).rejects.toMatchObject({ code: 'unexpected_response' });
  });
});

describe('setField', () => {
  it('puts the chosen values and returns the stored field', async () => {
    const api = installFakeApi();
    const entry = await setField('risk_preference', ['conservative']);

    expect(api.requests).toEqual(['PUT /api/v1/profile/risk_preference']);
    expect(api.bodies).toEqual([
      { key: 'PUT /api/v1/profile/risk_preference', body: { values: ['conservative'] } },
    ]);
    expect(entry.field).toBe('risk_preference');
    expect(entry.values).toEqual(['conservative']);
    expect(entry.source).toBe('edited');
  });

  it('passes a 422 on for values the server refuses', async () => {
    const api = installFakeApi();
    api.failWith('PUT /api/v1/profile/risk_preference', 422);
    await expect(setField('risk_preference', ['not_a_choice'])).rejects.toMatchObject({
      status: 422,
    });
  });

  it('rejects a reply that is not a valid field entry', async () => {
    serve({ field: 'risk_preference' });
    await expect(setField('risk_preference', ['conservative'])).rejects.toMatchObject({
      code: 'unexpected_response',
    });
  });
});

describe('forgetField', () => {
  it('deletes one field', async () => {
    const api = installFakeApi({ profileFields: [demoProfileField()] });
    await forgetField('risk_preference');
    expect(api.requests).toEqual(['DELETE /api/v1/profile/risk_preference']);

    const profile = await getProfile();
    expect(profile.fields).toEqual([]);
  });
});

describe('forgetAll', () => {
  it('deletes every field', async () => {
    const api = installFakeApi({
      profileFields: [demoProfileField(), demoProfileField({ field: 'debt_preference' })],
    });
    await forgetAll();
    expect(api.requests).toEqual(['DELETE /api/v1/profile']);

    const profile = await getProfile();
    expect(profile.fields).toEqual([]);
  });

  it('passes an unexpected failure on as an ApiError', async () => {
    const api = installFakeApi();
    api.failWith('DELETE /api/v1/profile', 500);
    await expect(forgetAll()).rejects.toBeInstanceOf(ApiError);
  });
});
