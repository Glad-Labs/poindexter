// Type declarations for @glad-labs/brand components.
// The implementation is plain JSX; these ambient types keep TS consumers
// (Next.js build-time typecheck on .tsx pages) from erroring on imports.

import type {
  ElementType,
  FC,
  ForwardRefExoticComponent,
  HTMLAttributes,
  ReactNode,
  Ref,
  RefAttributes,
} from 'react';

export interface EyebrowProps extends HTMLAttributes<HTMLSpanElement> {
  children: ReactNode;
  className?: string;
}
export const Eyebrow: FC<EyebrowProps>;

export interface DisplayProps extends HTMLAttributes<HTMLElement> {
  as?: ElementType;
  xl?: boolean;
  children: ReactNode;
  className?: string;
}
export const Display: FC<DisplayProps> & {
  Accent: FC<{ children: ReactNode }>;
};

export interface ButtonProps extends HTMLAttributes<HTMLElement> {
  variant?: 'primary' | 'secondary' | 'ghost';
  as?: ElementType;
  href?: string;
  target?: string;
  rel?: string;
  type?: 'button' | 'submit' | 'reset';
  disabled?: boolean;
  children: ReactNode;
  className?: string;
}
export const Button: ForwardRefExoticComponent<
  ButtonProps & RefAttributes<HTMLElement>
>;

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  accent?: 'cyan' | 'amber' | 'mint';
  children: ReactNode;
  className?: string;
}
export const Card: FC<CardProps> & {
  Meta: FC<{ children: ReactNode; className?: string }>;
  Tag: FC<{ children: ReactNode; className?: string }>;
  Title: FC<{
    as?: ElementType;
    children: ReactNode;
    className?: string;
  }>;
  Body: FC<{ children: ReactNode; className?: string }>;
};

export interface StatusProps extends HTMLAttributes<HTMLElement> {
  kind?: 'ok' | 'warn' | 'err';
  children: ReactNode;
  className?: string;
}
export const Status: FC<StatusProps>;
