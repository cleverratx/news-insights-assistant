import { useEffect, useState, type ComponentType, type SVGProps } from 'react';
import { Link, Navigate, NavLink, Outlet, useLocation } from 'react-router';
import { ArrowsRightLeftMIcon } from '@alfalab/icons-glyph/ArrowsRightLeftMIcon';
import { BanknotesMIcon } from '@alfalab/icons-glyph/BanknotesMIcon';
import { BulbMIcon } from '@alfalab/icons-glyph/BulbMIcon';
import { BurgerMIcon } from '@alfalab/icons-glyph/BurgerMIcon';
import { ChartLineAscMIcon } from '@alfalab/icons-glyph/ChartLineAscMIcon';
import { CrossMIcon } from '@alfalab/icons-glyph/CrossMIcon';
import { DocumentMIcon } from '@alfalab/icons-glyph/DocumentMIcon';
import { HouseMIcon } from '@alfalab/icons-glyph/HouseMIcon';
import { LogOutMIcon } from '@alfalab/icons-glyph/LogOutMIcon';
import { UsersMIcon } from '@alfalab/icons-glyph/UsersMIcon';
import { WalletMIcon } from '@alfalab/icons-glyph/WalletMIcon';

import { OrgSwitcher } from '@/app/OrgSwitcher';
import { signOut, useSession } from '@/app/session';
import type { ShellContext } from '@/app/shell-context';
import { freshImportant, useFeed } from '@/shared/api/feed';
import { useProfiles } from '@/shared/api/profiles';
import { DEMO_COMPANIES, ROLE_TITLES, type DemoUser } from '@/shared/lib/demo';
import { initials, plural } from '@/shared/lib/format';
import { clearSeen, useSeen } from '@/shared/lib/seen';
import { useDemoContext } from '@/shared/lib/url-state';

import styles from './AppShell.module.css';

type Icon = ComponentType<SVGProps<SVGSVGElement>>;

const SECTIONS: { to: string; title: string; icon: Icon; end?: boolean; news?: boolean }[] = [
  { to: '/', title: 'Главная', icon: HouseMIcon, end: true },
  { to: '/feed', title: 'Новости для бизнеса', icon: BulbMIcon, news: true },
  { to: '/positions', title: 'Моя должность', icon: UsersMIcon },
];

const STUBS: { title: string; icon: Icon }[] = [
  { title: 'Платежи', icon: ArrowsRightLeftMIcon },
  { title: 'Счета и выписки', icon: WalletMIcon },
  { title: 'Документы', icon: DocumentMIcon },
  { title: 'Зарплатный проект', icon: UsersMIcon },
  { title: 'Кредиты и гарантии', icon: BanknotesMIcon },
  { title: 'Валютный контроль', icon: ChartLineAscMIcon },
];

export function AppShell() {
  const user = useSession();
  const location = useLocation();
  if (!user) {
    const next = encodeURIComponent(`${location.pathname}${location.search}`);
    return <Navigate to={`/login?next=${next}`} replace />;
  }
  return <ShellLayout user={user} />;
}

function exit() {
  clearSeen();
  signOut();
}

function ShellLayout({ user }: { user: DemoUser }) {
  const demo = useDemoContext(user.role, user);
  const profiles = useProfiles(DEMO_COMPANIES);
  const feed = useFeed(demo.company, demo.role);
  const seen = useSeen(user.login, demo.company);
  const fresh = freshImportant(feed.data, seen);
  const location = useLocation();
  const [menuOpen, setMenuOpen] = useState(false);
  const [menuPath, setMenuPath] = useState(location.pathname);

  if (menuPath !== location.pathname) {
    setMenuPath(location.pathname);
    setMenuOpen(false);
  }

  useEffect(() => {
    if (!menuOpen) return;
    const close = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setMenuOpen(false);
    };
    document.addEventListener('keydown', close);
    return () => document.removeEventListener('keydown', close);
  }, [menuOpen]);

  const context: ShellContext = {
    ...demo,
    user,
    profiles,
    current: profiles.data?.find((result) => result.id === demo.company),
    feed,
    fresh,
  };

  return (
    <div className={styles.shell}>
      <aside className={styles.sidebar} data-open={menuOpen} aria-label="Меню интернет-банка">
        <div className={styles.brand}>
          <span className={styles.brandName}>Альфа-Бизнес</span>
          <span className={styles.demoMark}>демо</span>
          <button
            type="button"
            className={styles.closeMenu}
            aria-label="Закрыть меню"
            onClick={() => setMenuOpen(false)}
          >
            <CrossMIcon aria-hidden="true" />
          </button>
        </div>
        <nav aria-label="Разделы">
          <ul className={styles.menu}>
            {SECTIONS.map(({ to, title, icon: SectionIcon, end, news }) => (
              <li key={to}>
                <NavLink
                  to={{ pathname: to, search: demo.search }}
                  end={end}
                  className={({ isActive }) =>
                    isActive ||
                    (news &&
                      ['/competitors', '/analytics', '/knowledge'].includes(location.pathname))
                      ? `${styles.item} ${styles.itemActive}`
                      : styles.item
                  }
                >
                  <SectionIcon className={styles.itemIcon} aria-hidden="true" />
                  <span className={styles.itemTitle}>{title}</span>
                  {news && fresh.length > 0 && (
                    <span
                      className={styles.badge}
                      aria-label={`${fresh.length} ${plural(fresh.length, {
                        one: 'новое важное событие',
                        few: 'новых важных события',
                        many: 'новых важных событий',
                      })}`}
                    >
                      {fresh.length}
                    </span>
                  )}
                </NavLink>
              </li>
            ))}
          </ul>
          <ul className={styles.menu} aria-label="Недоступно в демо">
            {STUBS.map(({ title, icon: StubIcon }) => (
              <li key={title}>
                <span
                  className={`${styles.item} ${styles.itemStub}`}
                  aria-disabled="true"
                  title="В демо недоступно"
                >
                  <StubIcon className={styles.itemIcon} aria-hidden="true" />
                  <span className={styles.itemTitle}>{title}</span>
                </span>
              </li>
            ))}
          </ul>
        </nav>
      </aside>
      {menuOpen && (
        <button
          type="button"
          className={styles.overlay}
          aria-label="Закрыть меню"
          tabIndex={-1}
          onClick={() => setMenuOpen(false)}
        />
      )}
      <div className={styles.main}>
        <header className={styles.header}>
          <button
            type="button"
            className={styles.openMenu}
            aria-label="Открыть меню"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen(true)}
          >
            <BurgerMIcon aria-hidden="true" />
          </button>
          <OrgSwitcher
            company={demo.company}
            results={profiles.data}
            loading={profiles.loading}
            onChange={demo.setCompany}
          />
          <div className={styles.profile}>
            <span className={styles.avatar} aria-hidden="true">
              {initials(user.name)}
            </span>
            <Link
              className={styles.person}
              to={{ pathname: '/positions', search: demo.search }}
              style={{ color: 'inherit', textDecoration: 'none' }}
              title="Настроить должность"
            >
              <span className={styles.personName}>{user.name}</span>
              <span className={styles.personRole}>{user.title ?? ROLE_TITLES[user.role]}</span>
            </Link>
            <button type="button" className={styles.signOut} aria-label="Выйти" onClick={exit}>
              <LogOutMIcon aria-hidden="true" />
              <span className={styles.signOutText}>Выйти</span>
            </button>
          </div>
        </header>
        <main className={styles.content}>
          <Outlet context={context} />
        </main>
      </div>
    </div>
  );
}
