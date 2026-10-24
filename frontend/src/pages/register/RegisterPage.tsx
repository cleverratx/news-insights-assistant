import { useState } from 'react';
import { Link, useNavigate } from 'react-router';
import { Typography } from '@alfalab/core-components-typography';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { signInProfile } from '@/app/session';
import { PositionEditor } from '@/features/positions/PositionEditor';
import { PositionQuestions } from '@/features/positions/PositionQuestions';
import { positionSearch, type Position } from '@/features/positions/model';
import { companyName, useProfiles } from '@/shared/api/profiles';
import { DEMO_COMPANIES } from '@/shared/lib/demo';
import styles from '@/features/positions/Positions.module.css';

export function RegisterPage() {
  const [name, setName] = useState('');
  const [company, setCompany] = useState(DEMO_COMPANIES[0]);
  const [position, setPosition] = useState<Position>();
  const [savingAnswer, setSavingAnswer] = useState(false);
  const profiles = useProfiles(DEMO_COMPANIES);
  const navigate = useNavigate();
  const finish = () => {
    if (!position) return;
    signInProfile({
      login: `custom:${position.id}`,
      name: name.trim(),
      role: position.base_role,
      company: position.company_id,
      position: position.id,
      title: position.title,
    });
    void navigate({ pathname: '/feed', search: positionSearch(position) });
  };
  return (
    <div className={styles.registration}>
      <div className={styles.wizard}>
        <p className={styles.brand}>
          Альфа-Бизнес<span>демо</span>
        </p>
        <div>
          <Typography.Title tag="h1" view="small">
            {position ? 'Настроим новости под ваши задачи' : 'Регистрация профиля'}
          </Typography.Title>
          <p className={styles.lead}>
            {position
              ? 'Должность создана. Уточните контекст сейчас или вернитесь к нему позже.'
              : 'Расскажите о своей должности — подберём новости и акценты для вашей работы.'}
          </p>
        </div>
        <ol className={styles.steps} aria-label="Шаги регистрации">
          <li aria-current={!position ? 'step' : undefined}>1. Профиль и должность</li>
          <li aria-current={position ? 'step' : undefined}>2. Уточнение контекста</li>
        </ol>
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
            <PositionEditor
              key={company}
              company={company}
              identity={{ name, onName: setName }}
              onSaved={setPosition}
            />
            <p className={styles.hint}>
              Создаётся профиль для демонстрационного интернет-банка. Пароль не требуется.
            </p>
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
