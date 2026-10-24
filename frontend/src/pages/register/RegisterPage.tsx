import { useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { Typography } from '@alfalab/core-components-typography';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { PlusMIcon } from '@alfalab/icons-glyph/PlusMIcon';
import { signInProfile } from '@/app/session';
import { PositionEditor } from '@/features/positions/PositionEditor';
import { PositionQuestions } from '@/features/positions/PositionQuestions';
import { positionSearch, ROLE_DESCRIPTIONS, type Position } from '@/features/positions/model';
import { companyName, useProfiles } from '@/shared/api/profiles';
import { DEMO_COMPANIES, ROLE_TITLES, type Role } from '@/shared/lib/demo';
import styles from '@/features/positions/Positions.module.css';

export function RegisterPage() {
  const [params] = useSearchParams();
  const [name, setName] = useState('');
  const [company, setCompany] = useState(DEMO_COMPANIES[0]);
  const [selection, setSelection] = useState<Role | 'new'>(
    params.get('new') === '1' ? 'new' : 'ceo',
  );
  const [position, setPosition] = useState<Position>();
  const newRole = useRef<HTMLElement>(null);
  const [savingAnswer, setSavingAnswer] = useState(false);
  const profiles = useProfiles(DEMO_COMPANIES);
  const navigate = useNavigate();
  const validName = name.trim().length > 0 && Array.from(name.trim()).length <= 100;
  const finish = () => {
    if (!validName || (!position && selection === 'new')) return;
    const baseRole = position?.base_role ?? (selection === 'new' ? 'ceo' : selection);
    signInProfile({
      login: position ? `custom:${position.id}` : `profile:${crypto.randomUUID()}`,
      name: name.trim(),
      role: baseRole,
      company: position?.company_id ?? company,
      ...(position ? { position: position.id } : {}),
      title: position?.title ?? ROLE_TITLES[baseRole],
    });
    const search = position
      ? positionSearch(position)
      : `?${new URLSearchParams({ company, role: baseRole })}`;
    void navigate({ pathname: '/feed', search });
  };
  return (
    <div className={styles.registration}>
      <div className={styles.wizard}>
        <p className={styles.brand}>
          Альфа-Бизнес<span>демо</span>
        </p>
        <div>
          <Typography.Title tag="h1" view="small">
            {position ? 'Уточним контекст новой роли' : 'Регистрация профиля'}
          </Typography.Title>
          <p className={styles.lead}>
            {position
              ? 'Роль создана. Ответьте на вопросы сейчас или вернитесь к ним позже.'
              : 'Выберите одну из трёх базовых ролей или добавьте свою.'}
          </p>
        </div>
        {!position ? (
          <>
            <label className={styles.field}>
              <span>Компания</span>
              <select value={company} onChange={(e) => setCompany(e.target.value)}>
                {DEMO_COMPANIES.map((id) => (
                  <option key={id} value={id}>
                    {companyName(
                      profiles.data?.find((p) => p.id === id),
                      id,
                    )}
                  </option>
                ))}
              </select>
            </label>
            <label className={styles.field}>
              <span>Ваше имя</span>
              <input
                autoComplete="name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={100}
                required
              />
            </label>
            <fieldset className={styles.roleChoices}>
              <legend>Ваша роль</legend>
              {(Object.keys(ROLE_TITLES) as Role[]).map((role) => (
                <label className={styles.roleChoice} key={role} data-selected={selection === role}>
                  <input
                    type="radio"
                    name="role"
                    value={role}
                    checked={selection === role}
                    onChange={() => setSelection(role)}
                  />
                  <span>
                    <strong>{ROLE_TITLES[role]}</strong>
                    <span>{ROLE_DESCRIPTIONS[role]}</span>
                  </span>
                </label>
              ))}
              <button
                className={`${styles.roleChoice} ${styles.addRole}`}
                type="button"
                aria-pressed={selection === 'new'}
                onClick={() => {
                  setSelection('new');
                  requestAnimationFrame(() => newRole.current?.querySelector('input')?.focus());
                }}
              >
                <PlusMIcon aria-hidden="true" />
                <span>
                  <strong>Новая роль</strong>
                  <span>Другая должность и свой контекст</span>
                </span>
              </button>
            </fieldset>
            {selection === 'new' ? (
              <section ref={newRole} className={styles.newRole} aria-label="Новая роль">
                <h2>Расскажите о своей должности</h2>
                <p className={styles.hint}>
                  Введите любое название — например, «Директор по маркетингу» или «Юрист».
                </p>
                <PositionEditor
                  key={company}
                  company={company}
                  identity={{ name, onName: setName, renderField: false }}
                  onSaved={setPosition}
                />
              </section>
            ) : (
              <ButtonDesktop view="primary" size={48} block onClick={finish} disabled={!validName}>
                Начать работу
              </ButtonDesktop>
            )}
            <p className={styles.hint}>Демо-профиль. Пароль не требуется.</p>
            <Link to="/login">Уже есть демо-профиль? Войти</Link>
          </>
        ) : (
          <>
            <div className={styles.summary}>
              <h2>{position.title}</h2>
              <p>{position.context}</p>
            </div>
            <PositionQuestions position={position} onSaved={setPosition} onBusy={setSavingAnswer} />
            <ButtonDesktop view="primary" size={48} block onClick={finish} disabled={savingAnswer}>
              Начать работу
            </ButtonDesktop>
            <p className={styles.hint}>Настройки и ответы доступны в разделе «Моя должность».</p>
          </>
        )}
      </div>
    </div>
  );
}
