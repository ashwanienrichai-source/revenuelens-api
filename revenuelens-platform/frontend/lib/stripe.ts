import Stripe from 'stripe'
import { loadStripe } from '@stripe/stripe-js'

// ── Server-side Stripe client ────────────────────────────────────────
export const stripe = new Stripe(process.env.STRIPE_SECRET_KEY!, {
  apiVersion: '2023-10-16',
  typescript: true,
})

// ── Browser-side Stripe promise ──────────────────────────────────────
let stripePromise: ReturnType<typeof loadStripe>
export function getStripe() {
  if (!stripePromise) {
    stripePromise = loadStripe(process.env.NEXT_PUBLIC_STRIPE_PUBLISHABLE_KEY!)
  }
  return stripePromise
}

// ── Plan definitions ─────────────────────────────────────────────────
export const PLANS = {
  starter: {
    id: 'starter',
    name: 'Starter',
    price: 25,
    interval: 'month',
    priceId: process.env.STRIPE_STARTER_PRICE_ID!,
    features: [
      'Upload & analyze datasets',
      'Cohort analytics',
      'Customer analytics',
      'Revenue bridge',
      'Download reports',
      'Email support',
    ],
    highlighted: false,
  },
  pro: {
    id: 'pro',
    name: 'Pro',
    price: 99,
    interval: 'month',
    priceId: process.env.STRIPE_PRO_PRICE_ID!,
    features: [
      'Everything in Starter',
      'Unlimited datasets',
      'Advanced cohort analysis',
      'Price / volume decomposition',
      'API access',
      'Priority support',
      '1 consulting hour/month',
    ],
    highlighted: true,
  },
  enterprise: {
    id: 'enterprise',
    name: 'Enterprise',
    price: null,
    interval: 'month',
    priceId: null,
    features: [
      'Everything in Pro',
      'Custom integrations',
      'Dedicated analytics setup',
      'Custom data pipeline',
      'SLA guarantee',
      'Dedicated support',
    ],
    highlighted: false,
  },
} as const

export type PlanId = keyof typeof PLANS
