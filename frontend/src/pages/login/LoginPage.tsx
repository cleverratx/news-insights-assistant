import { useState, type FormEvent } from 'react';
import { Navigate, useSearchParams } from 'react-router';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { Typography } from '@alfalab/core-components-typography';

import { signIn, useSession } from '@/app/session';
import { DEMO_USERS, ROLE_TITLES } from '@/shared/lib/demo';
import { initials } from '@/shared/lib/format';

import styles from './LoginPage.module.css';

function safeNext(value: string | null): string {
  return value && value.startsWith('/') && !value.startsWith('//') ? value : '/';
}

export function LoginPage() {
  const user = useSession();
  const [params] = useSearchParams();
  const [login, setLogin] = useState(DEMO_USERS[0].login);
  const next = safeNext(params.get('next'));

  if (user) return <Navigate to={next} replace />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    signIn(login);
  };

  return (
    <div className={styles.page}>
      <form className={styles.plate} onSubmit={submit}>
        <p className={styles.brand}>
          Альфа-Бизнес <span className={styles.demoMark}>демо</span>
        </p>
        <Typography.Title tag="h1" view="small">
          Вход в интернет-банк
        </Typography.Title>
        <fieldset className={styles.users}>
          <legend className={styles.legend}>Тестовая учётная запись</legend>
          {DEMO_USERS.map((item) => (
            <label key={item.login} className={styles.user}>
              <input
                type="radio"
                name="login"
                value={item.login}
                checked={login === item.login}
                onChange={() => setLogin(item.login)}
                className={styles.radio}
              />
              <span className={styles.avatar} aria-hidden="true">
                {initials(item.name)}
              </span>
              <span className={styles.person}>
                <span className={styles.name}>{item.name}</span>
                <span className={styles.role}>{ROLE_TITLES[item.role]}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <ButtonDesktop type="submit" view="primary" size={56} block>
          Войти
        </ButtonDesktop>
        <p className={styles.note}>
          Демо-вход: полномочия пользователя считаются проверенными, пароль не нужен.
        </p>
      </form>
    </div>
  );
}
