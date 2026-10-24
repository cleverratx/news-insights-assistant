import { createBrowserRouter, RouterProvider } from 'react-router';

import { AppShell } from '@/app/AppShell';
import { FeedPage } from '@/pages/feed/FeedPage';
import { CompetitorsPage } from '@/pages/competitors/CompetitorsPage';
import { HomePage } from '@/pages/home/HomePage';
import { InsightPage } from '@/pages/insight/InsightPage';
import { LoginPage } from '@/pages/login/LoginPage';
import { NotFoundPage } from '@/pages/not-found/NotFoundPage';

const router = createBrowserRouter([
  { path: '/login', element: <LoginPage /> },
  {
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'feed', element: <FeedPage /> },
      { path: 'competitors', element: <CompetitorsPage /> },
      { path: 'news/:newsId', element: <InsightPage /> },
      { path: '*', element: <NotFoundPage /> },
    ],
  },
]);

export function App() {
  return <RouterProvider router={router} />;
}
