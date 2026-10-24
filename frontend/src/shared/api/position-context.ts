export function withPosition(path: string, role?: string | null, search?: string) {
  const context = new URLSearchParams(
    search ?? (typeof window === 'undefined' ? '' : window.location.search),
  );
  const [pathname, query = ''] = path.split('?');
  const match = /^\/companies\/([^/]+)\/(feed|insights|digest)$/.exec(pathname);
  const params = new URLSearchParams(query);
  const requestedRole = role ?? params.get('role');
  const id = context.get('position');
  if (
    match &&
    requestedRole &&
    (!context.has('role') || requestedRole === context.get('role')) &&
    decodeURIComponent(match[1]) === context.get('company') &&
    id &&
    /^position:[0-9a-f]{32}$/.test(id)
  ) {
    params.set('position', id);
  }
  return `${pathname}${params.size ? `?${params}` : ''}`;
}
