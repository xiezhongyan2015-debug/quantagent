"""Ensure future return values cannot affect any test-year prediction dates."""
from .daily import Data, OUT, write


class UnavailableFutureReturns:
    def __getitem__(self, _):
        raise AssertionError('Prediction eligibility accessed future return values')


def main():
    data = Data()
    expected = {year: data.split(year, 'test') for year in range(2021, 2027)}
    data.labels = UnavailableFutureReturns()
    for year, indices in expected.items():
        assert (data.split(year, 'test') == indices).all()
    report = {'status': 'passed', 'test_dates_independent_of_future_return_values': True,
              'sections': {str(year): len(indices) for year, indices in expected.items()}}
    write(OUT.parent/'prediction_eligibility_check.json', report)
    print(report, flush=True)


if __name__ == '__main__':
    main()
