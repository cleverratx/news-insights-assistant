import { useEffect, useRef, useState } from 'react';
import { ButtonDesktop } from '@alfalab/core-components-button/desktop';
import { errorMessage, isAbort } from '@/shared/api/client';
import styles from './ConfirmAction.module.css';

export function ConfirmAction({
  label = 'Удалить',
  title,
  description,
  confirmLabel = 'Удалить',
  disabled = false,
  onConfirm,
  onBusy,
}: {
  label?: string;
  title: string;
  description: string;
  confirmLabel?: string;
  disabled?: boolean;
  onConfirm: (signal: AbortSignal) => Promise<void>;
  onBusy?: (busy: boolean) => void;
}) {
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string>();
  const request = useRef<AbortController | null>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const confirm = useRef<HTMLButtonElement>(null);
  useEffect(() => () => request.current?.abort(), []);
  useEffect(() => {
    if (open) confirm.current?.focus();
  }, [open]);
  const run = async () => {
    if (request.current || disabled) return;
    const controller = new AbortController();
    request.current = controller;
    setBusy(true);
    onBusy?.(true);
    setError(undefined);
    try {
      await onConfirm(controller.signal);
      controller.signal.throwIfAborted();
      setOpen(false);
      trigger.current?.focus();
    } catch (caught) {
      if (!isAbort(caught)) setError(errorMessage(caught));
    } finally {
      if (!controller.signal.aborted) {
        request.current = null;
        setBusy(false);
        onBusy?.(false);
      }
    }
  };
  return (
    <div className={styles.root}>
      <button
        className={styles.trigger}
        ref={trigger}
        type="button"
        disabled={disabled || busy}
        aria-expanded={open}
        onClick={() => {
          setOpen(!open);
          setError(undefined);
        }}
      >
        {label}
      </button>
      {open && (
        <div className={styles.confirmation} role="group" aria-label={title}>
          <p className={styles.title}>{title}</p>
          <p>{description}</p>
          {error && (
            <p className={styles.error} role="alert">
              {error}
            </p>
          )}
          <div className={styles.actions}>
            <ButtonDesktop
              ref={confirm}
              size={40}
              view="secondary"
              className={styles.danger}
              disabled={disabled || busy}
              loading={busy}
              onClick={() => void run()}
            >
              {confirmLabel}
            </ButtonDesktop>
            <ButtonDesktop
              size={40}
              view="secondary"
              disabled={busy}
              onClick={() => {
                setOpen(false);
                setError(undefined);
                trigger.current?.focus();
              }}
            >
              Отмена
            </ButtonDesktop>
          </div>
        </div>
      )}
    </div>
  );
}
