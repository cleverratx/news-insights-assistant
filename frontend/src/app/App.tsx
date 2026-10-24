import { createBrowserRouter, RouterProvider } from 'react-router';

import { AppShell } from '@/app/AppShell';
import { FeedPage } from '@/pages/feed/FeedPage';
import { CompetitorsPage } from '@/pages/competitors/CompetitorsPage';
import { AnalyticsPage } from '@/pages/analytics/AnalyticsPage';
import { KnowledgePage } from '@/pages/knowledge/KnowledgePage';
import { HomePage } from '@/pages/home/HomePage';
import { InsightPage } from '@/pages/insight/InsightPage';
import { LoginPage } from '@/pages/login/LoginPage';
import { NotFoundPage } from '@/pages/not-found/NotFoundPage';
import { RegisterPage } from '@/pages/register/RegisterPage';
import { PositionsPage } from '@/pages/positions/PositionsPage';

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  { path: '/register', element: <RegisterPage /> },
  {
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'feed', element: <FeedPage /> },
      { path: 'competitors', element: <CompetitorsPage /> },
      { path: 'analytics', element: <AnalyticsPage /> },
      { path: 'knowledge', element: <KnowledgePage /> },
      { path: 'positions', element: <PositionsPage /> },
      { path: 'news/:newsId', element: <InsightPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]);

export function App() {
  return <RouterProvider router={router} />;
}
