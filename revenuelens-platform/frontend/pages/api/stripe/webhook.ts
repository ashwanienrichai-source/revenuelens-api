import type { NextApiRequest, NextApiResponse } from 'next'
import Stripe from 'stripe'
import { stripe } from '../../../lib/stripe'
import { createClient } from '@supabase/supabase-js'

export const config = { api: { bodyParser: false } }

const supabaseAdmin = createClient(
  process.env.NEXT_PUBLIC_SUPABASE_URL!,
  process.env.SUPABASE_SERVICE_ROLE_KEY!
)

// Read raw body without 'micro'
async function getRawBody(req: NextApiRequest): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    req.on('data', (chunk: Buffer) => chunks.push(chunk))
    req.on('end', () => resolve(Buffer.concat(chunks)))
    req.on('error', reject)
  })
}

type SubscriptionStatus = 'free' | 'starter' | 'pro' | 'enterprise'

function planFromPriceId(priceId: string): SubscriptionStatus {
  if (priceId === process.env.STRIPE_STARTER_PRICE_ID) return 'starter'
  if (priceId === process.env.STRIPE_PRO_PRICE_ID)     return 'pro'
  return 'starter'
}

async function updateUserSubscription(
  customerId: string,
  status: SubscriptionStatus,
  subscriptionId?: string
) {
  const { data: profile } = await supabaseAdmin
    .from('profiles')
    .select('id')
    .eq('stripe_customer_id', customerId)
    .single()

  if (!profile) return

  await supabaseAdmin
    .from('profiles')
    .update({
      subscription_status: status,
      subscription_id: subscriptionId || null,
      updated_at: new Date().toISOString(),
    })
    .eq('id', profile.id)
}

export default async function handler(req: NextApiRequest, res: NextApiResponse) {
  if (req.method !== 'POST') return res.status(405).end()

  const rawBody = await getRawBody(req)
  const sig = req.headers['stripe-signature']!

  let event: Stripe.Event
  try {
    event = stripe.webhooks.constructEvent(rawBody, sig, process.env.STRIPE_WEBHOOK_SECRET!)
  } catch (err: any) {
    return res.status(400).json({ error: `Webhook Error: ${err.message}` })
  }

  try {
    switch (event.type) {
      case 'checkout.session.completed': {
        const session = event.data.object as Stripe.Checkout.Session
        if (session.mode !== 'subscription') break
        const subscription = await stripe.subscriptions.retrieve(session.subscription as string)
        const priceId = subscription.items.data[0]?.price?.id
        await updateUserSubscription(session.customer as string, planFromPriceId(priceId), subscription.id)
        break
      }
      case 'customer.subscription.updated': {
        const subscription = event.data.object as Stripe.Subscription
        const priceId = subscription.items.data[0]?.price?.id
        const plan = subscription.status === 'active' ? planFromPriceId(priceId) : 'free'
        await updateUserSubscription(subscription.customer as string, plan, subscription.id)
        break
      }
      case 'customer.subscription.deleted': {
        const subscription = event.data.object as Stripe.Subscription
        await updateUserSubscription(subscription.customer as string, 'free')
        break
      }
    }
  } catch (err: any) {
    return res.status(500).json({ error: 'Webhook handler failed' })
  }

  return res.status(200).json({ received: true })
}
