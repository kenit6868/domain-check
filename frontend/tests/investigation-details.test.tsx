import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, expect, it } from 'vitest'
import { InvestigationDetails } from '../src/features/domain-check/components/InvestigationDetails'

afterEach(cleanup)

it('presents readable values and keeps long profile diagnostics collapsed', () => {
  render(<InvestigationDetails result={{ whois: { registrar: 'Fixture Registrar', name_servers: ['ns1.example.com'] },
    http_check: { status_code: 200, redirected: false }, domain_age_days: 0,
    cloaking: { verdict: 'NO_SIGNAL', score: 0, profiles: { mobile: { title: 'Observed title' } } } }} />)
  expect(screen.getByText('Fixture Registrar')).toBeVisible()
  expect(screen.getByText('ns1.example.com')).toBeVisible()
  expect(screen.getByText('HTTP status')).toBeVisible()
  expect(screen.getByText('200')).toBeVisible()
  expect(screen.getByText('Không')).toBeVisible()
  expect(screen.getByText('Observed title')).not.toBeVisible()
  expect(screen.getByText('Dữ liệu kỹ thuật đầy đủ').closest('details')).not.toHaveAttribute('open')
  expect(screen.getByText('Chi tiết profile & tín hiệu').closest('details')).not.toHaveAttribute('open')
})

it('handles missing, empty, and nested data without an object placeholder', () => {
  const { container } = render(<InvestigationDetails result={{ cert: {}, whois: { emails: [] },
    http_check: { redirected: null }, origin_ip_scan: { addresses: ['8.8.8.8'] } }} />)
  expect(screen.getByText('8.8.8.8')).toBeVisible()
  expect(screen.getAllByText('Chưa có dữ liệu').length).toBeGreaterThan(0)
  expect(container.textContent).not.toContain('[object Object]')
})
